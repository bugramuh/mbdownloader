"""HTTP bridge on 127.0.0.1 for the browser extensions: /ping /download /downloads /action /site.

Requests are parsed on server threads and handled on the UI thread (the store is not thread-safe).
Only browser-extension origins are accepted, and only with a loopback Host header.
"""
import json
import threading
import time
from concurrent.futures import Future
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QObject, Signal

from . import native_host_registry, paths, video
from .i18n import _
from .models import ACTIVE_STATES
from .qtutil import on_main
from .settings import site_key
from .store import DownloadStore, NewDownloadRequest

EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")
BROWSERS = ("chrome", "edge", "brave", "opera", "firefox", "safari")


def origin_allowed(origin: str | None) -> bool:
    """Requests without an Origin, with an empty one or the opaque 'null' origin are rejected."""
    if not origin or origin == "null":
        return False
    return origin.startswith(EXTENSION_ORIGINS)


def extension_request(headers: dict) -> bool:
    """Extension requests carry an extension Origin, or — when Chrome omits Origin on GETs covered by host
    permissions — no Origin plus the X-MBD-Extension header (a web page cannot send that without a preflight)."""
    origin = headers.get("origin")
    if origin_allowed(origin):
        return True
    return origin is None and bool(headers.get("x-mbd-extension"))


def browser_name(headers: dict, path: str) -> str:
    browser = "other"
    origin = headers.get("origin") or ""
    if origin.startswith("safari-web-extension://"):
        browser = "safari"
    elif origin.startswith("chrome-extension://"):
        browser = "chrome"
    elif origin.startswith("moz-extension://"):
        browser = "firefox"
    h = (headers.get("x-mbd-extension") or "").lower()
    if h in BROWSERS:
        browser = h
    q = parse_qs(urlsplit(path).query).get("browser")
    if q:
        browser = q[0].lower()
    return browser


class ServerController(QObject):
    status_changed = Signal()

    def __init__(self, store: DownloadStore):
        super().__init__()
        self.store = store
        self.settings = store.settings
        self.running = False
        self.status_text = _("Not started")
        self.connections: dict[str, float] = {}     # browser -> last contact
        self._httpd: ThreadingHTTPServer | None = None
        self._port = 0
        self.start()
        self.settings.changed.connect(self._settings_changed)

    def _settings_changed(self, key):
        if key == "serverPort" and self._clamped_port() != self._port:
            self.start()

    def _clamped_port(self) -> int:
        return max(1024, min(65535, int(self.settings.serverPort)))

    def is_connected(self, browser: str, within: float = 120) -> bool:
        t = self.connections.get(browser)
        return t is not None and time.time() - t < within

    def start(self):
        self.stop()
        port = self._clamped_port()
        self._port = port
        controller = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "MBDownloader"
            sys_version = ""

            def log_message(self, *args):
                pass

            def _serve(self):
                headers = {k.lower(): v for k, v in self.headers.items()}
                length = int(headers.get("content-length") or 0)
                if length > 16_000_000:
                    self.close_connection = True
                    return
                body = self.rfile.read(length) if length > 0 else b""
                fut: Future = Future()

                def run():
                    try:
                        fut.set_result(controller.handle(self.command, self.path, headers, body))
                    except Exception as e:  # noqa: BLE001
                        fut.set_result((500, {"ok": False, "error": str(e)}))
                on_main(run)
                try:
                    status, obj = fut.result(timeout=15)
                except Exception:  # noqa: BLE001
                    status, obj = 500, {"ok": False, "error": "timeout"}
                data = json.dumps(obj).encode("utf-8")
                self.send_response(status, "OK" if status == 200 else "Error")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                origin = headers.get("origin")
                # CORS headers only for extension origins; ordinary web pages get none.
                if origin_allowed(origin):
                    self.send_header("Access-Control-Allow-Origin", origin)
                    self.send_header("Vary", "Origin")
                    self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MBD-Extension, X-MBD-Extension-Id")
                    self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                    # Chrome Private/Local Network Access preflights for loopback targets.
                    self.send_header("Access-Control-Allow-Private-Network", "true")
                    self.send_header("Access-Control-Allow-Local-Network", "true")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(data)
                self.close_connection = True

            do_GET = do_POST = do_OPTIONS = do_PUT = do_DELETE = _serve

        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
            httpd.daemon_threads = True
        except OSError as e:
            self.running = False
            self.status_text = _("Error: {0} (port {1} may be in use)", e.strerror or str(e), str(port))
            self.status_changed.emit()
            return
        self._httpd = httpd
        threading.Thread(target=httpd.serve_forever, name="mbdownloader-bridge", daemon=True).start()
        self.running = True
        self.status_text = _("Listening on 127.0.0.1:{0}", str(port))
        self.status_changed.emit()

    def stop(self):
        if self._httpd:
            httpd, self._httpd = self._httpd, None
            threading.Thread(target=lambda: (httpd.shutdown(), httpd.server_close()), daemon=True).start()
        self.running = False

    def _host_allowed(self, host: str | None) -> bool:
        if not host:
            return False
        host = host.lower()
        p = self._port
        return host in (f"127.0.0.1:{p}", f"localhost:{p}", f"[::1]:{p}")

    def _note_connection(self, headers: dict, path: str):
        self.connections[browser_name(headers, path)] = time.time()
        # Chromium extension ids may call the native host that launches the app.
        ext_id = headers.get("x-mbd-extension-id")
        origin = headers.get("origin") or ""
        if origin.startswith("chrome-extension://"):
            ext_id = origin[len("chrome-extension://"):].split("/", 1)[0]
        if ext_id and native_host_registry.is_valid_chromium_id(ext_id.lower()):
            native_host_registry.note_extension_id(self.settings, ext_id.lower())
        self.status_changed.emit()

    def handle(self, method: str, path: str, headers: dict, body: bytes):
        if not extension_request(headers) or not self._host_allowed(headers.get("host")):
            return 403, {"ok": False, "error": "forbidden"}
        if method == "OPTIONS":
            return 200, {"ok": True}
        self._note_connection(headers, path)
        route = urlsplit(path).path
        store, settings = self.store, self.settings

        if method == "GET" and route == "/ping":
            return 200, {
                "ok": True, "app": "MBDownloader", "version": paths.VERSION, "platform": "windows",
                "ytdlp": video.locate_ytdlp(settings.ytdlpPath) is not None,
                "active": sum(1 for i in store.items if i.state in ACTIVE_STATES),
                "captureExtensions": settings.capture_extensions,
                # Downloads from these sites stay in the browser: permanently, or for a few minutes after a hand-off.
                "browserOnlySites": settings.browserOnlySites,
                "bypassHosts": store.bypass_hosts,
            }

        if method == "GET" and route == "/downloads":
            return 200, {"ok": True, "items": [
                {"id": i.id, "name": i.fileName, "state": i.state, "progress": i.progress, "speed": i.speed,
                 "total": i.totalBytes, "received": i.receivedBytes, "stage": i.stage}
                for i in store.items[:50]]}

        def parse():
            try:
                obj = json.loads(body.decode("utf-8") or "null")
                return obj if isinstance(obj, dict) else None
            except (ValueError, UnicodeDecodeError):
                return None

        if method == "POST" and route == "/download":
            obj = parse()
            url = obj.get("url") if obj else None
            if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")) or not urlsplit(url).hostname:
                return 400, {"ok": False, "error": "invalid url"}

            def s(k):
                v = obj.get(k)
                return v if isinstance(v, str) else None
            r = NewDownloadRequest(url=url, fileName=s("filename"), referer=s("referer"), userAgent=s("userAgent"),
                                   cookieHeader=s("cookieHeader"), kind="video" if obj.get("kind") == "video" else "file")
            if isinstance(obj.get("cookies"), list):
                r.cookies = [c for c in obj["cookies"] if isinstance(c, dict)]
            if isinstance(obj.get("headers"), dict):
                r.extraHeaders = {k: v for k, v in obj["headers"].items() if isinstance(k, str) and isinstance(v, str)}
            if isinstance(obj.get("segments"), int) and not isinstance(obj.get("segments"), bool):
                r.segments = obj["segments"]
            b = browser_name(headers, path)
            if b != "other":
                r.sourceBrowser = b
            item = store.add(r)
            return 200, {"ok": True, "id": item.id}

        if method == "POST" and route == "/site":
            obj = parse()
            host = obj.get("host") if obj else None
            on = obj.get("browserOnly") if obj else None
            if not isinstance(host, str) or not site_key(host) or not isinstance(on, bool):
                return 400, {"ok": False, "error": "host and browserOnly required"}
            settings.set_browser_only(host, on)
            return 200, {"ok": True, "browserOnlySites": settings.browserOnlySites}

        if method == "POST" and route == "/action":
            obj = parse()
            id_ = obj.get("id") if obj else None
            action = obj.get("action") if obj else None
            if not isinstance(id_, str) or not isinstance(action, str):
                return 400, {"ok": False, "error": "id and action required"}
            fn = {"pause": store.pause, "resume": store.resume, "cancel": store.cancel, "remove": store.remove}.get(action)
            if not fn:
                return 400, {"ok": False, "error": "unknown action"}
            fn(id_.upper())
            return 200, {"ok": True}

        return 404, {"ok": False, "error": "not found"}
