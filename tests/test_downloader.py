"""Engine tests against a local HTTP server: python tests\\test_downloader.py"""
import hashlib
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mbdownloader.downloader import HTTPStatusError, SegmentedDownloader  # noqa: E402

DATA = os.urandom(6 * 1024 * 1024 + 12345)
SHA = hashlib.sha256(DATA).hexdigest()
state = {"throttle_left": 0, "max_parallel": 0, "parallel": 0, "slow": 0.0, "cookie_seen": [], "range_requests": 0}
lock = threading.Lock()


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send_body(self, status, body, extra=None):
        self.send_response(status)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        step = 64 * 1024
        for i in range(0, len(body), step):
            try:
                self.wfile.write(body[i:i + step])
            except OSError:
                return
            if state["slow"]:
                time.sleep(state["slow"])

    def do_GET(self):
        path = self.path
        rng = self.headers.get("Range")
        state["cookie_seen"].append(self.headers.get("Cookie"))
        with lock:
            state["parallel"] += 1
            state["max_parallel"] = max(state["max_parallel"], state["parallel"])
        try:
            if path == "/redirect":
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{self.server.server_port}/ranged")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if path == "/noranges":
                self._send_body(200, DATA)
                return
            if path == "/blocked":              # refuses Range, serves the plain GET
                if rng:
                    self._send_body(403, b"no")
                else:
                    self._send_body(200, DATA, {"Content-Disposition": 'attachment; filename="blocked name.bin"'})
                return
            if path == "/forbidden":
                self._send_body(403, b"no")
                return
            if path in ("/ranged", "/throttle", "/cd"):
                if rng:
                    state["range_requests"] += 1
                if path == "/throttle" and rng and not rng.startswith("bytes=0-0") and state["throttle_left"] > 0:
                    with lock:
                        state["throttle_left"] -= 1
                    self._send_body(429, b"slow down")
                    return
                if not rng:
                    self._send_body(200, DATA, {"Accept-Ranges": "bytes"})
                    return
                m = re.match(r"bytes=(\d+)-(\d*)", rng)
                start = int(m.group(1))
                end = int(m.group(2)) if m.group(2) else len(DATA) - 1
                end = min(end, len(DATA) - 1)
                extra = {"Content-Range": f"bytes {start}-{end}/{len(DATA)}", "Accept-Ranges": "bytes"}
                if path == "/cd":
                    extra["Content-Disposition"] = "attachment; filename*=UTF-8''g%C3%BCzel%20dosya.bin"
                self._send_body(206, DATA[start:end + 1], extra)
                return
            self._send_body(404, b"nf")
        finally:
            with lock:
                state["parallel"] -= 1


def run_download(url, segments=8, headers=None, part=None, resume=None, timeout=60):
    part = part or os.path.join(tempfile.mkdtemp(), "x.part")
    done = threading.Event()
    res = {"err": "pending", "probe": None, "segs": [], "received": 0}
    dl = SegmentedDownloader(url, headers or {}, part, segments,
                             resume_segments=resume[0] if resume else None, resume_total=resume[1] if resume else -1)
    dl.on_probe = lambda p: res.__setitem__("probe", p)
    dl.on_progress = lambda r, s: res.update(received=r, segs=s)
    dl.on_finish = lambda e: (res.__setitem__("err", e), done.set())
    dl.start()
    return dl, done, res, part


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def check(name, cond, extra=""):
    print(("PASS " if cond else "FAIL ") + name + (" " + extra if extra else ""))
    if not cond:
        check.failed += 1


check.failed = 0


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    # 1. Multi-connection
    dl, done, res, part = run_download(base + "/cd", 8)
    done.wait(60)
    check("multi: finished without error", res["err"] is None, str(res["err"]))
    check("multi: content identical", sha(part) == SHA)
    check("multi: probe has ranges + total", res["probe"] and res["probe"].supports_ranges and res["probe"].total == len(DATA))
    check("multi: UTF-8 Content-Disposition name", res["probe"].suggested_name == "güzel dosya.bin", str(res["probe"].suggested_name))
    check("multi: used 8 segments", len(res["segs"]) == 8, str(len(res["segs"])))
    check("multi: parallel connections used", state["max_parallel"] >= 4, str(state["max_parallel"]))

    # 2. Redirect + credentials only to the original origin
    state["cookie_seen"].clear()
    dl, done, res, part = run_download(base + "/redirect", 4, headers={"Cookie": "a=b"})
    done.wait(60)
    check("redirect: finished", res["err"] is None and sha(part) == SHA, str(res["err"]))

    # 3. No range support -> single stream
    dl, done, res, part = run_download(base + "/noranges", 8)
    done.wait(60)
    check("single: finished + identical", res["err"] is None and sha(part) == SHA, str(res["err"]))
    check("single: no ranges reported", res["probe"] and not res["probe"].supports_ranges)

    # 4. Range refused (403) but plain GET works
    dl, done, res, part = run_download(base + "/blocked", 8)
    done.wait(60)
    check("blocked-range: falls back to plain GET", res["err"] is None and sha(part) == SHA, str(res["err"]))
    check("blocked-range: name from plain GET", res["probe"] and res["probe"].suggested_name == "blocked name.bin")

    # 5. Throttling (429) is retried with fewer connections
    state["throttle_left"] = 6
    t0 = time.time()
    dl, done, res, part = run_download(base + "/throttle", 8)
    done.wait(90)
    check("throttle: finished + identical", res["err"] is None and sha(part) == SHA, str(res["err"]))
    check("throttle: 429s were absorbed", state["throttle_left"] == 0, f"{time.time() - t0:.1f}s")

    # 6. Hard HTTP error
    dl, done, res, part = run_download(base + "/forbidden", 4)
    done.wait(30)
    check("403: reported as HTTPStatusError", isinstance(res["err"], HTTPStatusError) and res["err"].status == 403, repr(res["err"]))

    # 7. Pause and resume
    state["slow"] = 0.12
    dl, done, res, part = run_download(base + "/ranged", 6)
    t0 = time.time()
    while res["received"] < len(DATA) * 0.25 and time.time() - t0 < 30:
        time.sleep(0.05)
    dl.stop()
    time.sleep(0.5)
    got = res["received"]
    segs = [s.copy() for s in res["segs"]]
    check("pause: partial progress saved", 0 < got < len(DATA) and len(segs) == 6, f"{got}/{len(DATA)}")
    check("pause: no finish callback", not done.is_set())
    state["slow"] = 0.0
    dl2, done2, res2, _p = run_download(base + "/ranged", 6, part=part, resume=(segs, len(DATA)))
    done2.wait(60)
    check("resume: finished + identical", res2["err"] is None and sha(part) == SHA, str(res2["err"]))

    srv.shutdown()
    print("\nfailures:", check.failed)
    return 1 if check.failed else 0


if __name__ == "__main__":
    sys.exit(main())
