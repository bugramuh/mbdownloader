"""State of all downloads: queue, persistence, speed measurement. Used on the UI thread only."""
import json
import mimetypes
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QMessageBox

from . import cookies, paths, tools, video, winshell
from .downloader import HTTPStatusError, SegmentedDownloader, browser_might_help, describe_error
from .i18n import _
from .models import (ACTIVE_STATES, CANCELLED, COMPLETED, DOWNLOADING, FAILED, PAUSED, PROBING, QUEUED, DownloadItem,
                     category_label, file_ext)
from .qtutil import ask, on_main
from .settings import AppSettings, site_key

DEFAULT_UA = tools.DEFAULT_UA


@dataclass
class NewDownloadRequest:
    url: str
    fileName: str | None = None
    referer: str | None = None
    userAgent: str | None = None
    cookieHeader: str | None = None
    cookies: list | None = None
    kind: str = "file"
    extraHeaders: dict = field(default_factory=dict)
    segments: int | None = None
    sourceBrowser: str | None = None


def file_name_from_url(url: str) -> str:
    parts = urlsplit(url)
    name = unquote(parts.path.rstrip("/").rsplit("/", 1)[-1]) if parts.path else ""
    return name or parts.hostname or "download"


_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def sanitize(s: str) -> str:
    """Makes a name valid on Windows (no \\ / : * ? " < > |, no control chars, no trailing dot/space, no device names)."""
    n = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", s).strip().rstrip(". ")
    if n.startswith("."):
        n = "_" + n
    stem = n.split(".", 1)[0].upper()
    if stem in _RESERVED:
        n = "_" + n
    if len(n) > 200:
        ext = file_ext(n)
        base = n[: -(len(ext) + 1)] if ext else n
        n = base[:180] + ("." + ext if ext else "")
    return n or "download"


def unique_destination(path: str) -> str:
    if not os.path.exists(path):
        return path
    d, name = os.path.split(path)
    ext = file_ext(name)
    base = name[: -(len(ext) + 1)] if ext else name
    n = 2
    while True:
        cand = os.path.join(d, f"{base} ({n}).{ext}" if ext else f"{base} ({n})")
        if not os.path.exists(cand):
            return cand
        n += 1


def site_host_of(item: DownloadItem) -> str | None:
    """The site a download belongs to: the page it was started from, else its own host."""
    ref = item.referer
    host = urlsplit(ref).hostname if ref else None
    return site_key(host or urlsplit(item.url).hostname)


class DownloadStore(QObject):
    changed = Signal()                  # list or item contents changed
    show_detail = Signal(str)           # open the download window for an id
    item_completed = Signal(str, bool)  # id, auto-close
    notify = Signal(str, str)           # title, body

    def __init__(self, settings: AppSettings):
        super().__init__()
        self.settings = settings
        self.items: list[DownloadItem] = []
        self._downloaders: dict[str, SegmentedDownloader] = {}
        self._video_procs: dict[str, object] = {}
        self._video_phase: dict[str, list] = {}     # id -> [base, lastDownloaded, approxTotal]
        self._deno_waiters: list[str] = []
        self._deno_prompting = False
        self._deno_declined = False
        self._cookieless: set[str] = set()
        self._handoff_queue: list[str] = []
        self._handoff_prompting = False
        self._browser_bypass: dict[str, float] = {}
        self._samples: dict[str, tuple[int, float]] = {}
        self._tick_count = 0
        self._store_file = os.path.join(paths.support_dir(), "downloads.json")
        self._load()
        self._save_timer = QTimer(self, singleShot=True, interval=500)
        self._save_timer.timeout.connect(self.save)
        self._timer = QTimer(self, interval=500)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

    # --- Queries ---

    def item(self, id_: str) -> DownloadItem | None:
        return next((i for i in self.items if i.id == id_), None)

    def destination_dir(self, item: DownloadItem) -> str:
        """Final folder: base folder, plus a category subfolder when enabled."""
        if not self.settings.organizeByCategory:
            return item.directory
        return os.path.join(item.directory, category_label(item.category))

    def destination_path(self, item: DownloadItem) -> str:
        return os.path.join(self.destination_dir(item), item.fileName)

    @property
    def active_count(self) -> int:
        return sum(1 for i in self.items if i.state in ACTIVE_STATES)

    @property
    def overall_progress(self) -> float | None:
        m = [i for i in self.items if i.state in ACTIVE_STATES and i.totalBytes > 0]
        if not m:
            return None
        total = sum(i.totalBytes for i in m)
        got = sum(min(i.receivedBytes, i.totalBytes) for i in m)
        return got / total if total > 0 else None

    @property
    def total_speed(self) -> float:
        return sum(i.speed for i in self.items if i.state == DOWNLOADING)

    def prepare_download_folder(self):
        try:
            os.makedirs(self.settings.downloadDirectory, exist_ok=True)
        except OSError:
            pass

    def _changed(self):
        self.changed.emit()

    # --- Adding ---

    def add(self, req: NewDownloadRequest) -> DownloadItem:
        headers = dict(req.extraHeaders)
        if req.referer:
            headers["Referer"] = req.referer
        if req.userAgent:
            headers["User-Agent"] = req.userAgent
        if req.cookieHeader:
            headers["Cookie"] = req.cookieHeader
        headers.setdefault("User-Agent", DEFAULT_UA)

        name = req.fileName or file_name_from_url(req.url)
        if req.kind == "video":
            name = urlsplit(req.url).hostname or "video"
        item = DownloadItem(url=req.url, fileName=sanitize(name), directory=self.settings.downloadDirectory, kind=req.kind)
        item.headers = headers
        item.requestedSegments = req.segments or 0
        item.sourceBrowser = req.sourceBrowser
        if req.kind == "video" and req.cookies:
            path = os.path.join(cookies.cookies_dir(), f"{item.id}.txt")
            if cookies.write_cookie_file(path, req.cookies):
                item.cookieFile = path
        try:
            os.makedirs(item.directory, exist_ok=True)
        except OSError:
            pass
        self.items.insert(0, item)
        self._schedule_save()
        if self.settings.autoStart:
            self.pump_queue()
        self._changed()
        if self.settings.showDetailOnStart:
            self.show_detail.emit(item.id)
        return item

    def add_urls(self, text: str, kind: str, segments: int | None = None):
        for line in text.splitlines():
            s = line.strip()
            if s.lower().startswith(("http://", "https://")) and urlsplit(s).hostname:
                self.add(NewDownloadRequest(url=s, kind=kind, segments=segments))

    # --- Queue ---

    def pump_queue(self):
        slots = max(0, self.settings.maxConcurrent - self.active_count)
        for item in reversed(list(self.items)):
            if slots <= 0:
                break
            if item.state == QUEUED:
                self._start_item(item.id)
                slots -= 1

    def _start_item(self, id_: str):
        item = self.item(id_)
        if not item:
            return
        if item.kind == "video":
            self._start_video(item)
        else:
            self._start_file(item)

    def _start_file(self, item: DownloadItem):
        resumable = item.supportsRanges and item.totalBytes > 0 and item.segments and os.path.exists(item.part_path)
        dl = SegmentedDownloader(
            item.url, item.headers, item.part_path,
            item.requestedSegments if item.requestedSegments > 0 else self.settings.segmentCount,
            resume_segments=item.segments if resumable else None,
            resume_total=item.totalBytes if resumable else -1)
        id_ = item.id

        def current():
            return self._downloaders.get(id_) is dl

        def on_probe(p):
            on_main(lambda: current() and self._handle_probe(id_, p))

        def on_progress(received, segs):
            def upd():
                if not current():
                    return
                it = self.item(id_)
                if it:
                    it.receivedBytes = received
                    it.segments = segs
                    if it.state == PROBING:
                        it.state = DOWNLOADING
            on_main(upd)

        def on_finish(err):
            on_main(lambda: current() and self._handle_finish(id_, err))

        dl.on_probe, dl.on_progress, dl.on_finish = on_probe, on_progress, on_finish
        self._downloaders[id_] = dl
        item.state = DOWNLOADING if resumable else PROBING
        item.errorMessage = None
        item.speed = 0
        if not resumable:
            item.receivedBytes = 0
            item.segments = []
        dl.start()
        self._changed()

    def _handle_probe(self, id_: str, p):
        item = self.item(id_)
        if not item:
            return
        item.totalBytes = p.total
        item.supportsRanges = p.supports_ranges
        item.state = DOWNLOADING
        if p.suggested_name:
            item.fileName = sanitize(p.suggested_name)
        elif p.final_url and p.final_url != item.url and item.fileName == sanitize(file_name_from_url(item.url)):
            cand = file_name_from_url(p.final_url)
            if file_ext(cand):
                item.fileName = sanitize(cand)
        if not file_ext(item.fileName) and p.content_type:
            ext = mimetypes.guess_extension(p.content_type)
            if ext and ext != ".bin":
                item.fileName += ext
        self._changed()

    def _handle_finish(self, id_: str, err):
        self._downloaders.pop(id_, None)
        item = self.item(id_)
        if not item:
            return
        if err is None:
            dest_dir = self.destination_dir(item)
            try:
                os.makedirs(dest_dir, exist_ok=True)
                dest = unique_destination(os.path.join(dest_dir, item.fileName))
                os.replace(item.part_path, dest)
                try:
                    import ctypes
                    ctypes.windll.kernel32.SetFileAttributesW(dest, 0x80)   # FILE_ATTRIBUTE_NORMAL (part file was hidden)
                except Exception:
                    pass
                winshell.mark_of_the_web(dest, item.url, item.referer)
                item.fileName = os.path.basename(dest)
                item.finalPath = dest
                item.state = COMPLETED
                item.completedAt = time.time()
                item.speed = 0
                if item.totalBytes <= 0:
                    item.totalBytes = item.receivedBytes
                self._notify_completed(item)
                self.item_completed.emit(id_, self.settings.autoCloseDetail)
            except OSError as e:
                item.state = FAILED
                item.errorMessage = describe_error(e)
        else:
            if isinstance(err, HTTPStatusError):
                item.lastHTTPStatus = err.status
            if item.state not in (PAUSED, CANCELLED):
                item.state = FAILED
                item.errorMessage = describe_error(err)
                item.speed = 0
                if browser_might_help(err):
                    self._offer_browser_handoff(id_)
        self._schedule_save()
        self.pump_queue()
        self._changed()

    # --- Hand-off to the browser ---

    @property
    def bypass_hosts(self) -> list[str]:
        now = time.time()
        self._browser_bypass = {k: v for k, v in self._browser_bypass.items() if v > now}
        return list(self._browser_bypass)

    def _offer_browser_handoff(self, id_: str):
        if id_ not in self._handoff_queue:
            self._handoff_queue.append(id_)
        if self._handoff_prompting:
            return
        self._handoff_prompting = True
        QTimer.singleShot(0, self._prompt_next_handoff)

    def _prompt_next_handoff(self):
        while self._handoff_queue:
            id_ = self._handoff_queue.pop(0)
            item = self.item(id_)
            if not item or item.state != FAILED:
                continue
            site = site_host_of(item) or item.url
            idx, always = ask(
                None, paths.APP_NAME, _("Download in the browser instead?"),
                _("“{0}” could not be downloaded: {1}\n\nSome sites (for example WeTransfer) only allow their downloads in a browser. MBDownloader can hand this download back to the browser.",
                  item.fileName, item.errorMessage or ""),
                [_("Download in Browser"), _("Retry Here"), _("Close")],
                checkbox=_("Always leave downloads from {0} to the browser", site),
                icon=QMessageBox.Warning, escape_index=2)
            if always:
                self.settings.set_browser_only(site_host_of(item), True)
            if idx == 0:
                self.hand_off_to_browser(id_)
            elif idx == 1:
                self.resume(id_)
        self._handoff_prompting = False

    def hand_off_to_browser(self, id_: str):
        """Opens the download in the browser it came from and lets the extension pass it through for a few minutes."""
        item = self.item(id_)
        if not item:
            return
        until = time.time() + 180
        for host in (urlsplit(item.url).hostname, urlsplit(item.referer).hostname if item.referer else None):
            k = site_key(host)
            if k:
                self._browser_bypass[k] = until
        # A refused signed link is usually spent (one-shot token): reopen the page so the site issues a fresh one.
        target = item.url
        page = item.referer
        if item.lastHTTPStatus in (401, 403, 404, 410) and page and page.lower().startswith(("http://", "https://")):
            target = page
        winshell.open_in_browser(item.sourceBrowser, target)
        self.remove(id_)

    # --- Video (yt-dlp) ---

    def _start_video(self, item: DownloadItem):
        id_ = item.id
        exe = video.locate_ytdlp(self.settings.ytdlpPath)
        if not exe:
            item.state = FAILED
            item.errorMessage = _("yt-dlp not found. Use “Install yt-dlp” in Settings ▸ Video.")
            self._changed()
            QTimer.singleShot(0, self.pump_queue)
            return
        if video.is_youtube(item.url) and not video.locate_deno() and not self.settings.denoPromptSuppressed and not self._deno_declined:
            item.state = DOWNLOADING
            item.errorMessage = None
            item.speed = 0
            item.stage = _("Waiting for the JavaScript runtime (deno)…")
            if id_ not in self._deno_waiters:
                self._deno_waiters.append(id_)
            if not self._deno_prompting:
                self._deno_prompting = True
                QTimer.singleShot(0, self._prompt_for_deno)
            self._changed()
            return
        use_cookies = id_ not in self._cookieless
        item.state = DOWNLOADING
        item.errorMessage = None
        item.receivedBytes = 0
        item.speed = 0
        item.totalBytes = -1
        item.stage = _("Fetching video information…")
        if use_cookies:
            item.log = ""   # keep the failed attempt's output visible above the retry
        out_dir = self.destination_dir(item)
        os.makedirs(out_dir, exist_ok=True)
        self._video_phase[id_] = [0, 0, -1]

        def on_progress(p):
            def upd():
                ph = self._video_phase.get(id_)
                it = self.item(id_)
                if ph is None or not it:
                    return
                if p.downloaded < ph[1]:
                    ph[0] += ph[1]      # new file started
                ph[1] = p.downloaded
                received = ph[0] + p.downloaded
                it.receivedBytes = received
                phase_total = ph[0] + p.total if p.total > 0 else -1
                it.totalBytes = max(ph[2], phase_total, received)
                it.speed = p.speed
            on_main(upd)

        def on_line(line):
            def upd():
                it = self.item(id_)
                if not it:
                    return
                it.log = (it.log + line + "\n")[-6000:]
                st = video.stage_for(line)
                if st:
                    it.stage = st
                if line.startswith("MCFMT|"):
                    parts = line.split("|")
                    try:
                        n = float(parts[5]) if len(parts) > 5 else 0
                    except ValueError:
                        n = 0
                    if n > 0:
                        ph = self._video_phase.setdefault(id_, [0, 0, -1])
                        ph[2] = int(n)
                        if it.totalBytes < int(n):
                            it.totalBytes = int(n)
                if "Destination: " in line:
                    # Show the final title; strip yt-dlp's intermediate ".f137" format suffix.
                    name = os.path.basename(line.split("Destination: ", 1)[1])
                    it.fileName = re.sub(r"\.f[0-9]+[-a-z0-9]*(?=\.[a-z0-9]+$)", "", name)
                if line.startswith('[Merger] Merging formats into "') and line.count('"') >= 2:
                    it.fileName = os.path.basename(line[line.index('"') + 1: line.rindex('"')])
            on_main(upd)

        def on_finish(path, err):
            on_main(lambda: self._handle_video_finish(id_, path, err))

        proc = video.run(exe, item.url, out_dir, self.settings.ytdlp_format_arguments, item.headers, item.cookieFile,
                         use_cookies, on_progress, on_line, on_finish)
        if proc:
            self._video_procs[id_] = proc
        self._changed()

    def _handle_video_finish(self, id_: str, path: str | None, err):
        self._video_procs.pop(id_, None)
        self._video_phase.pop(id_, None)
        item = self.item(id_)
        if not item or item.state != DOWNLOADING:   # the user paused/cancelled
            return
        if err is None:
            item.state = COMPLETED
            item.completedAt = time.time()
            item.speed = 0
            if path:
                item.finalPath = path
                item.fileName = os.path.basename(path)
                winshell.mark_of_the_web(path, item.url, item.referer)
                try:
                    size = os.path.getsize(path)
                    item.totalBytes = item.receivedBytes = size
                except OSError:
                    pass
            elif item.totalBytes > 0:
                item.receivedBytes = item.totalBytes
            self._notify_completed(item)
            self.item_completed.emit(id_, self.settings.autoCloseDetail)
        else:
            msg = describe_error(err)
            if (id_ not in self._cookieless and video.is_youtube(item.url) and video.is_signed_in_client_failure(msg)
                    and cookies.has_cookies(item.cookieFile, urlsplit(item.url).hostname)):
                self._cookieless.add(id_)
                item.log += _("→ YouTube rejected the signed-in request; retrying without cookies…") + "\n"
                self._start_video(item)
                return
            item.state = FAILED
            item.errorMessage = msg
            item.speed = 0
        self._schedule_save()
        self.pump_queue()
        self._changed()

    def _prompt_for_deno(self):
        """Asks once whether to install deno, which yt-dlp needs for YouTube, then starts the waiting downloads."""
        idx, suppress = ask(
            None, paths.APP_NAME, _("Install the JavaScript runtime for YouTube?"),
            _("YouTube now requires yt-dlp to run JavaScript (deno) to unlock video streams. Without it, YouTube downloads may fail with errors such as “The page needs to be reloaded” or “n challenge solving failed”.\n\nMBDownloader can download deno ({0}) from its official GitHub releases and keep it in its own application data folder.", tools.DENO_SIZE),
            [_("Install deno"), _("Not Now")], checkbox=_("Don’t ask again"), escape_index=1)
        if suppress:
            self.settings.denoPromptSuppressed = True
        if idx != 0:
            self._deno_declined = True
            self._resume_deno_waiters()
            return
        for id_ in self._deno_waiters:
            it = self.item(id_)
            if it:
                it.stage = _("Installing deno…")
        self._changed()

        def done(_version, err):
            if err:
                self._deno_declined = True   # don't loop on a failing install; Settings ▸ Video can retry
                for id_ in self._deno_waiters:
                    it = self.item(id_)
                    if it:
                        it.log += _("deno could not be installed: {0}", describe_error(err)) + "\n"
            self._resume_deno_waiters()
        tools.installer.install_deno(done)

    def _resume_deno_waiters(self):
        self._deno_prompting = False
        waiting, self._deno_waiters = self._deno_waiters, []
        for id_ in waiting:
            it = self.item(id_)
            if it and it.state == DOWNLOADING:
                self._start_video(it)

    # --- User actions ---

    def pause(self, id_: str):
        item = self.item(id_)
        if not item or not (item.state in ACTIVE_STATES or item.state == QUEUED):
            return
        item.state = PAUSED
        item.speed = 0
        dl = self._downloaders.get(id_)
        if dl:
            dl.stop()
        proc = self._video_procs.pop(id_, None)
        if proc:
            video.kill_tree(proc)
        self._schedule_save()
        self.pump_queue()
        self._changed()

    def resume(self, id_: str):
        item = self.item(id_)
        if not item or item.state not in (PAUSED, FAILED, CANCELLED):
            return
        item.state = QUEUED
        item.errorMessage = None
        item.lastHTTPStatus = None
        self._cookieless.discard(id_)
        self.pump_queue()
        self._changed()

    def start(self, id_: str):
        """Starts a queued download (added while 'Start new downloads immediately' is off) when a slot is free."""
        item = self.item(id_)
        if item and item.state == QUEUED and self.active_count < self.settings.maxConcurrent:
            self._start_item(id_)

    def cancel(self, id_: str):
        item = self.item(id_)
        if not item:
            return
        dl = self._downloaders.pop(id_, None)
        if dl:
            dl.stop()
        proc = self._video_procs.pop(id_, None)
        if proc:
            video.kill_tree(proc)
        _remove(item.part_path)
        item.state = CANCELLED
        item.speed = 0
        item.receivedBytes = 0
        item.segments = []
        self._schedule_save()
        self.pump_queue()
        self._changed()

    def remove(self, id_: str, delete_file: bool = False):
        item = self.item(id_)
        if not item:
            return
        dl = self._downloaders.pop(id_, None)
        if dl:
            dl.stop()
        proc = self._video_procs.pop(id_, None)
        if proc:
            video.kill_tree(proc)
        _remove(item.part_path)
        if item.cookieFile:
            _remove(item.cookieFile)
        if delete_file and item.finalPath and os.path.exists(item.finalPath):
            winshell.move_to_recycle_bin(item.finalPath)   # recoverable
        self.items.remove(item)
        self._samples.pop(id_, None)
        self._schedule_save()
        self.pump_queue()
        self._changed()

    def pause_all(self):
        for i in [i for i in self.items if i.state in ACTIVE_STATES or i.state == QUEUED]:
            self.pause(i.id)

    def resume_all(self):
        for i in [i for i in self.items if i.state in (PAUSED, FAILED)]:
            self.resume(i.id)
        self.pump_queue()   # also starts downloads added while "Start new downloads immediately" is off

    def clear_finished(self):
        for i in [i for i in self.items if i.state in (COMPLETED, CANCELLED)]:
            self.remove(i.id)

    def reveal(self, item: DownloadItem):
        if item.finalPath and os.path.exists(item.finalPath):
            winshell.reveal(item.finalPath)
        else:
            winshell.reveal(self.destination_dir(item))

    def open(self, item: DownloadItem):
        if item.finalPath:
            winshell.open_path(item.finalPath)

    def shutdown(self):
        for id_, dl in list(self._downloaders.items()):
            item = self.item(id_)
            if item:
                item.state = PAUSED
            dl.stop()
        for id_, proc in list(self._video_procs.items()):
            item = self.item(id_)
            if item:
                item.state = PAUSED
            video.kill_tree(proc)
        self._downloaders.clear()
        self._video_procs.clear()
        self.save()

    # --- Speed ---

    def _tick(self):
        now = time.monotonic()
        active = set()
        for item in self.items:
            if item.state != DOWNLOADING or item.kind == "video":
                continue
            active.add(item.id)
            s = self._samples.get(item.id)
            if s:
                dt = now - s[1]
                if dt > 0:
                    inst = max(0.0, (item.receivedBytes - s[0]) / dt)
                    item.speed = inst if item.speed == 0 else item.speed * 0.6 + inst * 0.4
            self._samples[item.id] = (item.receivedBytes, now)
        self._samples = {k: v for k, v in self._samples.items() if k in active}
        self._tick_count += 1
        if any(i.state in ACTIVE_STATES for i in self.items):
            self._changed()
            if self._tick_count % 10 == 0:
                self.save()     # segment state to disk every 5 s

    # --- Persistence ---

    def _load(self):
        try:
            with open(self._store_file, encoding="utf-8") as f:
                arr = [DownloadItem.from_json(d) for d in json.load(f)]
        except (OSError, ValueError, TypeError, KeyError):
            return
        for i in arr:
            if i.state in ACTIVE_STATES or i.state == QUEUED:
                i.state = PAUSED
            i.speed = 0
        self.items = arr
        self._cleanup_orphan_parts()

    def _cleanup_orphan_parts(self):
        d = self.settings.downloadDirectory
        try:
            names = os.listdir(d)
        except OSError:
            return
        known = {f".dm-{i.id}.part" for i in self.items}
        for n in names:
            if n.startswith(".dm-") and n.endswith(".part") and n not in known:
                _remove(os.path.join(d, n))

    def save(self):
        tmp = self._store_file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump([i.to_json() for i in self.items], f, ensure_ascii=False)
            os.replace(tmp, self._store_file)
        except OSError:
            pass

    def _schedule_save(self):
        self._save_timer.start()

    # --- Notification ---

    def _notify_completed(self, item: DownloadItem):
        if self.settings.notifyOnComplete:
            self.notify.emit(_("Download completed"), item.fileName)


def _remove(p):
    try:
        os.remove(p)
    except OSError:
        pass
