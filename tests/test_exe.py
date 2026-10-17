"""Smoke test of the packaged app: python test_exe.py <dist\\MBDownloader>"""
import functools
import json
import os
import struct
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import winreg
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

dist = sys.argv[1]
PORT = 48978
TMP = tempfile.mkdtemp(prefix="mbd-exe-")
APPDATA = os.path.join(TMP, "appdata")
DL = os.path.join(TMP, "dl")
os.makedirs(os.path.join(APPDATA, "MBDownloader"))
json.dump({"serverPort": PORT, "onboardingDone": True, "downloadDirectory": DL, "showDetailOnStart": False,
           "notifyOnComplete": False, "watchClipboard": False, "checkForUpdates": False},
          open(os.path.join(APPDATA, "MBDownloader", "settings.json"), "w"))
env = dict(os.environ, APPDATA=APPDATA, MBD_LANG="en")
ok_all = []


def check(name, cond, extra=""):
    ok_all.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (" " + str(extra)[:300] if extra else ""), flush=True)


def call(method, path, body=None):
    h = {"Host": f"127.0.0.1:{PORT}", "X-MBD-Extension": "chrome"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=data, method=method, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def host_msg(host, msg):
    body = json.dumps(msg).encode()
    p = subprocess.run([host], input=struct.pack("=I", len(body)) + body, capture_output=True, timeout=30)
    out = p.stdout
    (n,) = struct.unpack("=I", out[:4])
    return json.loads(out[4:4 + n])


# Test video: 2 seconds of test pattern, served over HTTP
media = os.path.join(TMP, "media")
os.makedirs(media)
ff = os.path.join(dist, "tools", "ffmpeg.exe")
subprocess.run([ff, "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=15", "-f", "lavfi", "-i",
                "sine=frequency=440:duration=2", "-pix_fmt", "yuv420p", "-shortest", os.path.join(media, "clip.mp4")], check=True)
srv = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(SimpleHTTPRequestHandler, directory=media))
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_port}"

exe = os.path.join(dist, "MBDownloader.exe")
check("dist: files present", all(os.path.exists(os.path.join(dist, f)) for f in
      ("MBDownloader.exe", "mbdownloader-native-host.exe", "Extension\\manifest.json", "tools\\yt-dlp.exe", "tools\\ffmpeg.exe")))
proc = subprocess.Popen([exe, "--background"], env=env, cwd=dist)
try:
    pong = None
    for _i in range(60):
        try:
            pong = call("GET", "/ping")
            break
        except OSError:
            time.sleep(0.5)
    check("app: bridge answers /ping", pong and pong.get("ok"), pong and {k: pong[k] for k in ("app", "version", "ytdlp")})
    check("app: finds bundled yt-dlp", pong and pong.get("ytdlp") is True)
    check("app: started hidden in tray (process alive)", proc.poll() is None)

    # Native host
    host = os.path.join(dist, "mbdownloader-native-host.exe")
    check("host: ping", host_msg(host, {"action": "ping"}) == {"ok": True})
    check("host: unknown action", host_msg(host, {"action": "x"}).get("ok") is False)
    r = host_msg(host, {"action": "launchApp"})
    check("host: launchApp -> ok", r.get("ok") is True, r)
    time.sleep(3)
    pids = subprocess.run(["tasklist", "/FI", "IMAGENAME eq MBDownloader.exe", "/FO", "CSV", "/NH"], capture_output=True, text=True).stdout
    # PyInstaller's onedir bootloader keeps one process (plus none for the child); a second instance exits at once.
    check("app: second launch did not leave a duplicate instance", pids.count("MBDownloader.exe") <= 2, pids.strip().replace("\n", " | "))

    # Registry registration
    def reg(path):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path + r"\com.mbdownloader.host") as k:
                return winreg.QueryValueEx(k, "")[0]
        except OSError:
            return None
    chrome_manifest = reg(r"Software\Google\Chrome\NativeMessagingHosts")
    check("registry: Chrome native host registered", chrome_manifest and os.path.exists(chrome_manifest), chrome_manifest)
    if chrome_manifest:
        m = json.load(open(chrome_manifest))
        check("registry: manifest points at host exe", os.path.normcase(m["path"]) == os.path.normcase(host) and m["allowed_origins"], m)
    check("registry: Edge + Firefox registered", reg(r"Software\Microsoft\Edge\NativeMessagingHosts") and reg(r"Software\Mozilla\NativeMessagingHosts"))

    # File download
    file_url = base + "/clip.mp4"
    j = call("POST", "/download", {"url": file_url, "filename": "clip.mp4"})
    for _i in range(100):
        it = next((x for x in call("GET", "/downloads")["items"] if x["id"] == j["id"]), None)
        if it and it["state"] in ("completed", "failed"):
            break
        time.sleep(0.2)
    check("file download completed", it and it["state"] == "completed", it)

    # Video download through bundled yt-dlp + ffmpeg
    j = call("POST", "/download", {"url": file_url, "kind": "video"})
    it = None
    for _i in range(240):
        it = next((x for x in call("GET", "/downloads")["items"] if x["id"] == j["id"]), None)
        if it and it["state"] in ("completed", "failed"):
            break
        time.sleep(0.5)
    check("video download (yt-dlp) completed", it and it["state"] == "completed", it)
    found = [os.path.join(d, f) for d, _s, fs in os.walk(DL) for f in fs if f.lower().endswith(".mp4")]
    check("video file exists in Video folder", any(os.sep + "Video" + os.sep in f for f in found), found)
finally:
    proc.kill()
    subprocess.run(["taskkill", "/F", "/T", "/IM", "MBDownloader.exe"], capture_output=True)
    # Remove the registry entries this test created (they point at the temp build)
    for p in (r"Software\Google\Chrome\NativeMessagingHosts", r"Software\Microsoft\Edge\NativeMessagingHosts",
              r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts", r"Software\Chromium\NativeMessagingHosts",
              r"Software\Mozilla\NativeMessagingHosts"):
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, p + r"\com.mbdownloader.host")
        except OSError:
            pass
print("\nfailures:", ok_all.count(False))
sys.exit(1 if False in ok_all else 0)
