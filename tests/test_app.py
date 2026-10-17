"""App integration test (offscreen Qt, isolated %APPDATA%): python tests\\test_app.py [screenshot_dir]"""
import hashlib
import json
import os
import sys
import tempfile
import threading
import time
import urllib.request

root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, root)
sys.path.insert(0, os.path.join(root, "tests"))

TMP = tempfile.mkdtemp(prefix="mbdownloader-test-")
os.environ["APPDATA"] = os.path.join(TMP, "appdata")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("MBD_LANG", "en")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else None

from http.server import ThreadingHTTPServer  # noqa: E402

import test_downloader as td  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from mbdownloader import i18n, qtutil  # noqa: E402

PORT = 48977
EXT = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
results = []


def check(name, cond, extra=""):
    results.append(cond)
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra) if extra else ""), flush=True)


def call(method, path, body=None, headers=None, host=None):
    h = {"Host": host or f"127.0.0.1:{PORT}"}
    h.update(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read() or b"{}"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), dict(e.headers)


def scenario(base, ctx, done):
    try:
        ext = {"Origin": EXT, "X-MBD-Extension": "chrome"}
        # --- Bridge security ---
        check("bridge: no Origin -> 403", call("GET", "/ping")[0] == 403)
        check("bridge: web origin -> 403", call("GET", "/ping", headers={"Origin": "https://evil.example"})[0] == 403)
        check("bridge: null origin -> 403", call("GET", "/ping", headers={"Origin": "null"})[0] == 403)
        check("bridge: foreign Host -> 403", call("GET", "/ping", headers=ext, host="evil.example")[0] == 403)
        st, j, hd = call("GET", "/ping", headers=ext)
        check("bridge: extension origin -> 200", st == 200 and j.get("ok") and j.get("app") == "MBDownloader", j)
        check("bridge: CORS only for extension", hd.get("Access-Control-Allow-Origin") == EXT)
        st, j, _h = call("GET", "/ping", headers={"X-MBD-Extension": "firefox"})
        check("bridge: header-only (no Origin) -> 200", st == 200 and "captureExtensions" in j and "zip" in j["captureExtensions"])
        st, _j, hd = call("OPTIONS", "/download", headers={"Origin": EXT, "Access-Control-Request-Method": "POST"})
        check("bridge: preflight", st == 200 and "Access-Control-Allow-Private-Network" in hd)
        check("bridge: bad url -> 400", call("POST", "/download", {"url": "ftp://x/y"}, ext)[0] == 400)
        check("bridge: unknown route -> 404", call("GET", "/nope", headers=ext)[0] == 404)

        # --- Download through the bridge ---
        st, j, _h = call("POST", "/download", {"url": base + "/ranged", "filename": "test file.zip", "referer": base + "/page",
                                               "cookieHeader": "a=b"}, ext)
        check("download: accepted", st == 200 and j.get("ok") and j.get("id"), j)
        id_ = j["id"]
        final = None
        for _i in range(300):
            items = call("GET", "/downloads", headers=ext)[1]["items"]
            it = next((x for x in items if x["id"] == id_), None)
            if it and it["state"] == "completed":
                final = it
                break
            time.sleep(0.1)
        check("download: completed", final is not None, final)
        dest = os.path.join(ctx.settings.downloadDirectory, "Archives", "test file.zip")
        check("download: saved in category folder", os.path.isfile(dest), dest)
        check("download: content identical", os.path.isfile(dest) and hashlib.sha256(open(dest, "rb").read()).hexdigest() == td.SHA)
        try:
            zone = open(dest + ":Zone.Identifier", encoding="utf-8").read()
            check("download: Mark of the Web content", "ZoneId=3" in zone and "HostUrl=" in zone, zone.replace("\r\n", " | "))
        except OSError as e:
            check("download: Mark of the Web content", False, e)

        # --- Duplicate name gets a unique name ---
        st, j2, _h = call("POST", "/download", {"url": base + "/ranged", "filename": "test file.zip"}, ext)
        for _i in range(300):
            it = next((x for x in call("GET", "/downloads", headers=ext)[1]["items"] if x["id"] == j2["id"]), None)
            if it and it["state"] == "completed":
                break
            time.sleep(0.1)
        check("download: duplicate -> 'test file (2).zip'", it and it["name"] == "test file (2).zip", it and it["name"])

        # --- Pause / resume / cancel / remove ---
        td.state["slow"] = 0.6
        st, j3, _h = call("POST", "/download", {"url": base + "/ranged", "filename": "slow.bin"}, ext)
        time.sleep(2.0)
        call("POST", "/action", {"id": j3["id"], "action": "pause"}, ext)
        time.sleep(0.5)
        it = next(x for x in call("GET", "/downloads", headers=ext)[1]["items"] if x["id"] == j3["id"])
        check("action: pause", it["state"] == "paused" and 0 < it["received"] < it["total"], it)
        td.state["slow"] = 0.0
        call("POST", "/action", {"id": j3["id"], "action": "resume"}, ext)
        for _i in range(300):
            it = next(x for x in call("GET", "/downloads", headers=ext)[1]["items"] if x["id"] == j3["id"])
            if it["state"] == "completed":
                break
            time.sleep(0.1)
        check("action: resume completes", it["state"] == "completed", it)
        check("action: unknown action -> 400", call("POST", "/action", {"id": j3["id"], "action": "explode"}, ext)[0] == 400)
        call("POST", "/action", {"id": j3["id"], "action": "remove"}, ext)
        time.sleep(0.3)
        ids = [x["id"] for x in call("GET", "/downloads", headers=ext)[1]["items"]]
        check("action: remove", j3["id"] not in ids)

        # --- Failing download surfaces an error ---
        st, j4, _h = call("POST", "/download", {"url": base + "/forbidden", "filename": "nope.zip"}, ext)
        for _i in range(100):
            it = next(x for x in call("GET", "/downloads", headers=ext)[1]["items"] if x["id"] == j4["id"])
            if it["state"] == "failed":
                break
            time.sleep(0.1)
        check("failure: 403 -> failed state", it["state"] == "failed", it)

        # --- Site list ---
        st, j5, _h = call("POST", "/site", {"host": "www.WeTransfer.com", "browserOnly": True}, ext)
        check("site: browser-only added (normalized)", j5.get("browserOnlySites") == ["wetransfer.com"], j5)
        st, j6, _h = call("GET", "/ping", headers=ext)
        check("site: /ping lists it", "wetransfer.com" in j6["browserOnlySites"])
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("scenario crashed", False, e)
    finally:
        done.set()


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), td.H)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    i18n.init()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    qtutil.init_invoker()
    from mbdownloader import tools
    from mbdownloader.app import AppContext
    # Isolated settings before the context starts the bridge
    ctx_settings_dir = os.path.join(os.environ["APPDATA"], "MBDownloader")
    os.makedirs(ctx_settings_dir, exist_ok=True)
    with open(os.path.join(ctx_settings_dir, "settings.json"), "w", encoding="utf-8") as f:
        json.dump({"serverPort": PORT, "downloadDirectory": os.path.join(TMP, "dl"), "onboardingDone": True,
                   "showDetailOnStart": True, "notifyOnComplete": False}, f)
    ctx = AppContext(app)
    check("server: listening", ctx.server.running, ctx.server.status_text)

    done = threading.Event()
    threading.Thread(target=scenario, args=(base, ctx, done), daemon=True).start()

    def finish():
        if not done.is_set():
            QTimer.singleShot(100, finish)
            return
        shots(ctx)
        ctx.quit()
    QTimer.singleShot(100, finish)
    app.exec()
    print("\nfailures:", results.count(False))
    return 1 if False in results else 0


def shots(ctx):
    """Builds every window once (catches construction errors) and saves screenshots when asked."""
    try:
        ctx.main.show()
        ctx.settings.showDetailOnStart = True
        first = ctx.store.items[0].id if ctx.store.items else None
        if first:
            ctx.detail.show_download(first)
        ctx.show_settings()
        ctx.show_wizard()
        QApplication.processEvents()
        check("ui: main window has rows", ctx.main.proxy.rowCount() >= 3, ctx.main.proxy.rowCount())
        wins = {"main": ctx.main, "detail": ctx.detail, "settings": ctx._settings, "wizard": ctx._wizard}
        for step in range(1, 4):
            ctx._wizard.step = min(step, ctx._wizard.step_count - 1)
            ctx._wizard._render()
        ctx._wizard.step = 0
        ctx._wizard._render()
        QApplication.processEvents()
        if SHOTS:
            os.makedirs(SHOTS, exist_ok=True)
            for n, w in wins.items():
                w.grab().save(os.path.join(SHOTS, n + ".png"))
        check("ui: windows constructed", True)
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check("ui: windows constructed", False, e)


if __name__ == "__main__":
    sys.exit(main())
