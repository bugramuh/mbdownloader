"""Downloads one file with N parallel HTTP Range requests.

A coordinator thread probes the server, then runs one worker thread per active segment. Workers report
to the coordinator through a queue; segment state is shared under a lock. Callbacks (on_probe,
on_progress, on_finish) run on worker threads — the caller marshals them to the UI thread.
"""
import ctypes
import os
import queue
import re
import threading
import time
import urllib.request
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit

import urllib3

from .i18n import _
from .models import SegmentInfo

CHUNK = 256 * 1024
MAX_ATTEMPTS = 12
REDIRECTS = {301, 302, 303, 307, 308}
CREDENTIAL_HEADERS = {"cookie", "authorization", "proxy-authorization"}


class DownloadFailure(Exception):
    def __init__(self, message: str, local: bool = False):
        super().__init__(message)
        self.message = message
        self.local = local      # disk/file problem: a browser would not do better

    def __str__(self):
        return self.message


class HTTPStatusError(Exception):
    def __init__(self, status: int):
        super().__init__(status)
        self.status = status

    def __str__(self):
        return _("Server returned HTTP {0}", self.status)


@dataclass
class ProbeResult:
    total: int
    supports_ranges: bool
    suggested_name: str | None
    content_type: str | None
    final_url: str | None


def describe_error(e: BaseException) -> str:
    if isinstance(e, (DownloadFailure, HTTPStatusError)):
        return str(e)
    if isinstance(e, urllib3.exceptions.MaxRetryError) and e.reason is not None:
        e = e.reason
    if isinstance(e, urllib3.exceptions.NameResolutionError):
        return _("The server could not be found.")
    if isinstance(e, urllib3.exceptions.NewConnectionError):
        return _("Could not connect to the server.")
    if isinstance(e, (urllib3.exceptions.ReadTimeoutError, urllib3.exceptions.ConnectTimeoutError, TimeoutError)):
        return _("The request timed out.")
    if isinstance(e, urllib3.exceptions.SSLError):
        return _("A secure connection to the server could not be made.")
    if isinstance(e, OSError) and e.strerror:
        return e.strerror
    return str(e) or e.__class__.__name__


def browser_might_help(e: BaseException) -> bool:
    """Server refusals and odd responses: a browser (its own cookies, the site's own flow) may succeed.
    Local problems (disk, no network) would fail there too."""
    if isinstance(e, HTTPStatusError):
        return True
    if isinstance(e, DownloadFailure):
        return not e.local
    if isinstance(e, urllib3.exceptions.MaxRetryError) and e.reason is not None:
        e = e.reason
    if isinstance(e, (urllib3.exceptions.NewConnectionError, urllib3.exceptions.ProtocolError)):
        return False
    return isinstance(e, urllib3.exceptions.HTTPError)


_pool = None
_pool_lock = threading.Lock()


def pool() -> urllib3.PoolManager:
    """Shared connection pool; uses the Windows (Internet Options) proxy when one is configured."""
    global _pool
    with _pool_lock:
        if _pool is None:
            kw = dict(num_pools=64, maxsize=32, block=False, retries=False,
                      timeout=urllib3.Timeout(connect=30, read=30))
            proxies = urllib.request.getproxies()
            proxy = proxies.get("https") or proxies.get("http")
            if proxy:
                if "://" not in proxy:
                    proxy = "http://" + proxy
                _pool = urllib3.ProxyManager(proxy, **kw)
            else:
                _pool = urllib3.PoolManager(**kw)
        return _pool


def _set_sparse(f) -> None:
    """Marks the part file sparse so preallocating a large file does not zero-fill it on NTFS."""
    try:
        import msvcrt
        handle = msvcrt.get_osfhandle(f.fileno())
        returned = ctypes.c_ulong(0)
        ctypes.windll.kernel32.DeviceIoControl(ctypes.c_void_p(handle), 0x900C4, None, 0, None, 0, ctypes.byref(returned), None)
    except Exception:
        pass


def fix_header_encoding(s: str) -> str:
    """http.client decodes header bytes as Latin-1, but servers send UTF-8 (or legacy cp1254): re-decode."""
    try:
        raw = s.encode("latin-1")
    except UnicodeEncodeError:
        return s    # already real Unicode
    for enc in ("utf-8", "cp1254"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            pass
    return s


def unquote_name(s: str) -> str:
    """Percent-decodes as UTF-8, falling back to cp1254 (legacy Turkish links)."""
    for enc in ("utf-8", "cp1254"):
        try:
            return unquote(s, encoding=enc, errors="strict")
        except UnicodeDecodeError:
            pass
    return unquote(s)


def file_name_from_content_disposition(header: str | None) -> str | None:
    if not header:
        return None
    header = fix_header_encoding(header)
    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", header, re.I)
    if m:
        try:
            d = unquote(m.group(2).strip(), encoding=m.group(1) or "utf-8", errors="strict")
        except (LookupError, UnicodeDecodeError):
            d = unquote_name(m.group(2).strip())
        d = d.strip('"')
        if d:
            return d
    m = re.search(r'filename\s*=\s*"([^"]*)"', header, re.I)
    if m:
        return unquote_name(m.group(1)) or None
    m = re.search(r"filename\s*=\s*([^;]+)", header, re.I)
    if m:
        return unquote_name(m.group(1).strip()) or None
    return None


def _is_throttle(status: int) -> bool:
    return status in (408, 425, 429, 500, 502, 503, 504)


class SegmentedDownloader:
    def __init__(self, url: str, headers: dict, part_path: str, segment_count: int,
                 resume_segments: list | None = None, resume_total: int = -1, min_segment_size: int = 512 * 1024):
        self.url = url
        self.headers = dict(headers)
        self.part_path = part_path
        self.segment_count = max(1, segment_count)
        self.min_segment_size = min_segment_size
        self.resume_segments = [s.copy() for s in (resume_segments or [])]
        self.resume_total = resume_total

        self.on_probe = None        # (ProbeResult)
        self.on_progress = None     # (received, [SegmentInfo])
        self.on_finish = None       # (exception or None)

        self._request_url = url
        self._lock = threading.Lock()
        self._segments: list[SegmentInfo] = []
        self._single_received = 0
        self._mode = "idle"
        self._stopped = threading.Event()
        self._finished = False
        self._fallback_done = False
        self._probe_from_single = False
        self._last_report = 0.0
        self._responses: set = set()
        self._generation = 0        # bumped when segment workers must abandon their work (fallback/stop)

    # --- Control ---

    def start(self):
        threading.Thread(target=self._run, name="mbdownloader-download", daemon=True).start()

    def stop(self):
        """Pause or cancel. The last segment state is reported through on_progress."""
        with self._lock:
            if self._finished or self._stopped.is_set():
                return
            self._stopped.set()
            self._generation += 1
            responses = list(self._responses)
        for r in responses:
            self._abort_response(r)
        self._emit_progress(force=True)

    # --- Helpers ---

    def _same_origin(self, url: str) -> bool:
        a, b = urlsplit(url), urlsplit(self.url)
        return (a.hostname or "").lower() == (b.hostname or "").lower() and a.scheme.lower() == b.scheme.lower() and a.port == b.port

    def _headers_for(self, url: str, rng: str | None) -> dict:
        allow_credentials = self._same_origin(url)
        h = {k: v for k, v in self.headers.items() if allow_credentials or k.lower() not in CREDENTIAL_HEADERS}
        h["Accept-Encoding"] = "identity"
        h["Cache-Control"] = "no-cache"
        if rng:
            h["Range"] = rng
        return h

    def _open(self, rng: str | None):
        """GET with manual redirects; credentials only go to the origin the download was requested for."""
        url = self._request_url
        for _hop in range(12):
            r = pool().request("GET", url, headers=self._headers_for(url, rng), preload_content=False,
                               redirect=False, decode_content=False)
            if r.status in REDIRECTS and r.headers.get("Location"):
                loc = urljoin(url, r.headers["Location"])
                self._abort_response(r)
                url = loc
                continue
            r.mbdownloader_url = url
            return r
        raise DownloadFailure(_("Too many redirects"))

    def _track(self, r):
        with self._lock:
            self._responses.add(r)

    def _untrack(self, r):
        with self._lock:
            self._responses.discard(r)
        self._abort_response(r)

    @staticmethod
    def _abort_response(r):
        try:
            if hasattr(r, "shutdown"):
                r.shutdown()
        except Exception:
            pass
        try:
            r.close()
        except Exception:
            pass

    def _received(self) -> int:
        if self._mode == "single":
            return self._single_received
        return sum(s.received for s in self._segments)

    def _emit_progress(self, force=False):
        now = time.monotonic()
        with self._lock:
            if not force and now - self._last_report < 0.2:
                return
            self._last_report = now
            received = self._received()
            segs = [s.copy() for s in self._segments]
        if self.on_progress:
            self.on_progress(received, segs)

    def _open_file(self, create: bool, preallocate: int | None):
        d = os.path.dirname(self.part_path)
        try:
            os.makedirs(d, exist_ok=True)
            if create or not os.path.exists(self.part_path):
                with open(self.part_path, "wb") as f:
                    if preallocate and preallocate > 0:
                        _set_sparse(f)
                        f.truncate(preallocate)
            elif preallocate and preallocate > 0 and os.path.getsize(self.part_path) < preallocate:
                with open(self.part_path, "r+b") as f:
                    f.truncate(preallocate)
            # Hide the part file like the macOS dot-file.
            try:
                ctypes.windll.kernel32.SetFileAttributesW(self.part_path, 0x2)
            except Exception:
                pass
        except OSError as e:
            raise DownloadFailure(_("Could not create file: {0}", self.part_path) + f" ({describe_error(e)})", local=True)

    def _finish_success(self):
        with self._lock:
            if self._finished or self._stopped.is_set():
                return
            self._finished = True
            self._generation += 1
        self._emit_progress(force=True)
        if self.on_finish:
            self.on_finish(None)

    def _fail(self, e: BaseException):
        with self._lock:
            if self._finished or self._stopped.is_set():
                return
            self._finished = True
            self._generation += 1
            responses = list(self._responses)
        for r in responses:
            self._abort_response(r)
        self._emit_progress(force=True)
        if self.on_finish:
            self.on_finish(e)

    def _done(self) -> bool:
        return self._finished or self._stopped.is_set()

    # --- Flow ---

    def _run(self):
        try:
            can_resume = self.resume_total > 0 and self.resume_segments and os.path.exists(self.part_path)
            if can_resume:
                self._segments = self.resume_segments
                self._open_file(create=False, preallocate=self.resume_total)
                self._mode = "multi"
                self._run_multi()
            else:
                self._probe()
        except BaseException as e:  # noqa: BLE001 - any failure ends the download
            if not self._done():
                self._fail(e)

    def _probe(self):
        self._mode = "probing"
        r = self._open("bytes=0-0")
        try:
            status = r.status
            headers = r.headers
            final_url = r.mbdownloader_url
        finally:
            self._abort_response(r)
        if self._done():
            return
        if not (200 <= status <= 299):
            # Some servers (signed / one-shot links, anti download-manager checks) refuse a Range request
            # but serve the plain GET a browser would send: try that once before giving up.
            if not self._fallback_done and not _is_throttle(status):
                self._fallback_done = True
                self._probe_from_single = True
                self._open_file(create=True, preallocate=None)
                self._mode = "single"
                self._run_single()
            else:
                self._fail(HTTPStatusError(status))
            return

        total, ranges = -1, False
        cr = headers.get("Content-Range")
        if status == 206 and cr and "/" in cr:
            try:
                total = int(cr.rsplit("/", 1)[1].strip())
                ranges = True
            except ValueError:
                pass
        if not ranges:
            try:
                total = int(headers.get("Content-Length", "-1"))
            except ValueError:
                total = -1
            ranges = total > 0 and "bytes" in headers.get("Accept-Ranges", "").lower()
        self._request_url = final_url
        ctype = (headers.get("Content-Type") or "").split(";")[0].strip() or None
        if self.on_probe:
            self.on_probe(ProbeResult(total, ranges, file_name_from_content_disposition(headers.get("Content-Disposition")), ctype, final_url))

        count = max(1, min(self.segment_count, total // self.min_segment_size)) if ranges else 1
        if ranges and count > 1:
            self._segments = self._make_segments(total, count)
            self._open_file(create=True, preallocate=total)
            self._mode = "multi"
            self._run_multi()
        else:
            self._open_file(create=True, preallocate=None)
            self._mode = "single"
            self._run_single()

    @staticmethod
    def _make_segments(total: int, count: int) -> list[SegmentInfo]:
        size = total // count
        out = []
        for i in range(count):
            start = i * size
            end = total - 1 if i == count - 1 else start + size - 1
            out.append(SegmentInfo(start, end, 0))
        return out

    # --- Single stream ---

    def _run_single(self):
        self._single_received = 0
        r = self._open(None)
        self._track(r)
        try:
            if not (200 <= r.status <= 299):
                self._fail(HTTPStatusError(r.status))
                return
            try:
                expected = int(r.headers.get("Content-Length", "-1"))
            except ValueError:
                expected = -1
            if self._probe_from_single:
                self._probe_from_single = False
                self._request_url = r.mbdownloader_url
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip() or None
                if self.on_probe:
                    self.on_probe(ProbeResult(expected, False, file_name_from_content_disposition(r.headers.get("Content-Disposition")),
                                              ctype, r.mbdownloader_url))
            with open(self.part_path, "r+b") as f:
                f.seek(0)
                f.truncate(0)
                while not self._done():
                    try:
                        data = r.read(CHUNK)
                    except Exception as e:  # noqa: BLE001
                        if self._done():
                            return
                        self._fail(e)
                        return
                    if not data:
                        break
                    try:
                        f.write(data)
                    except OSError as e:
                        self._fail(DownloadFailure(describe_error(e), local=True))
                        return
                    self._single_received += len(data)
                    self._emit_progress()
            if self._done():
                return
            if expected > 0 and self._single_received < expected:
                self._fail(DownloadFailure(_("Connection closed early ({0}/{1} bytes)", self._single_received, expected)))
                return
            self._finish_success()
        finally:
            self._untrack(r)

    # --- Multiple connections ---

    def _run_multi(self):
        segs = self._segments
        pending = [i for i, s in enumerate(segs) if not s.done]
        if not pending:
            self._finish_success()
            return
        events: queue.Queue = queue.Queue()
        active: set[int] = set()
        retries: dict[int, int] = {}
        active_limit = self.segment_count
        throttled_until = 0.0
        gen = self._generation

        while not self._done():
            now = time.monotonic()
            if throttled_until <= now:
                while len(active) < active_limit and pending:
                    i = pending.pop(0)
                    if segs[i].done:
                        continue
                    active.add(i)
                    threading.Thread(target=self._segment_worker, args=(i, gen, events), daemon=True).start()
            if not active and not pending:
                if all(s.done for s in segs):
                    self._finish_success()
                break
            try:
                ev = events.get(timeout=0.25)
            except queue.Empty:
                self._emit_progress()
                continue
            kind, i = ev[0], ev[1]
            if kind == "ok206":
                retries[i] = 0
            elif kind == "fatal":
                self._fail(ev[2])
                return
            elif kind == "status":
                active.discard(i)
                status = ev[2]
                if status == 200:
                    self._fallback_to_single()
                    return
                if _is_throttle(status):
                    # Server throttling (429/503): fewer connections, put the segment back in the queue.
                    active_limit = max(1, min(active_limit, len(active)) - 1)
                    attempt = retries.get(i, 0) + 1
                    retries[i] = attempt
                    if attempt > MAX_ATTEMPTS:
                        self._fail(HTTPStatusError(status))
                        return
                    throttled_until = time.monotonic() + min(15.0, 1.5 * attempt)
                    if i not in pending:
                        pending.insert(0, i)
                else:
                    # 401/403/410…: the server accepted the probe but refuses parallel connections — continue on one.
                    if self._fallback_done:
                        self._fail(HTTPStatusError(status))
                    else:
                        self._fallback_to_single()
                    return
            elif kind == "finished":
                active.discard(i)
                err = ev[2]
                if segs[i].done:
                    active_limit = min(self.segment_count, active_limit + 1)
                else:
                    attempt = retries.get(i, 0) + 1
                    retries[i] = attempt
                    if attempt > MAX_ATTEMPTS:
                        self._fail(err or DownloadFailure(_("Segment {0} could not be completed", i + 1)))
                        return
                    throttled_until = max(throttled_until, time.monotonic() + min(10.0, float(attempt)))
                    if i not in pending:
                        pending.append(i)
            self._emit_progress()

    def _segment_worker(self, i: int, gen: int, events: queue.Queue):
        seg = self._segments[i]
        r = None
        try:
            r = self._open(f"bytes={seg.start + seg.received}-{seg.end}")
            self._track(r)
            if self._generation != gen:
                return
            if r.status != 206:
                events.put(("status", i, r.status))
                return
            events.put(("ok206", i))
            with open(self.part_path, "r+b") as f:
                f.seek(seg.start + seg.received)
                while self._generation == gen and not seg.done:
                    data = r.read(CHUNK)
                    if not data:
                        break
                    remaining = seg.length - seg.received
                    if len(data) > remaining:
                        data = data[:remaining]
                    try:
                        f.write(data)
                    except OSError as e:
                        events.put(("fatal", i, DownloadFailure(describe_error(e), local=True)))
                        return
                    with self._lock:
                        seg.received += len(data)
                    self._emit_progress()
            events.put(("finished", i, None))
        except BaseException as e:  # noqa: BLE001
            events.put(("finished", i, e))
        finally:
            if r is not None:
                self._untrack(r)

    def _fallback_to_single(self):
        """The server ignored Range (200) or refused the segment requests: switch to one stream."""
        if self._fallback_done:
            return
        self._fallback_done = True
        with self._lock:
            self._generation += 1
            responses = list(self._responses)
        for r in responses:
            self._abort_response(r)
        with self._lock:
            self._segments = []
        self._open_file(create=True, preallocate=None)
        self._mode = "single"
        self._run_single()
