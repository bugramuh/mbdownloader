"""Native messaging host for Chrome, Edge, Brave, Opera and Firefox.

The browser starts this tool when the extension calls runtime.sendNativeMessage; it understands one request,
{"action": "launchApp"}, which starts MBDownloader in the background so the extension can hand over a
download while the app was not running. Nothing else (URLs, cookies) passes through here.

Protocol: each message is a 32-bit length in native byte order followed by UTF-8 JSON, on stdin/stdout.
"""
import json
import os
import struct
import subprocess
import sys

APP_EXE = "MBDownloader.exe"
DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def read_message():
    header = sys.stdin.buffer.read(4)
    if len(header) != 4:
        return None
    (length,) = struct.unpack("=I", header)
    if length == 0 or length > 1 << 20:
        return None
    body = sys.stdin.buffer.read(length)
    if len(body) != length:
        return None
    try:
        obj = json.loads(body.decode("utf-8"))
        return obj if isinstance(obj, dict) else {}
    except ValueError:
        return {}


def send(obj):
    body = json.dumps(obj).encode("utf-8")
    sys.stdout.buffer.write(struct.pack("=I", len(body)) + body)
    sys.stdout.buffer.flush()


def app_path() -> str:
    """mbdownloader-native-host.exe lives next to MBDownloader.exe."""
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
    for d in (here, os.path.dirname(here)):
        p = os.path.join(d, APP_EXE)
        if os.path.isfile(p):
            return p
    return ""


def launch_app() -> bool:
    """Starts the app in the background (tray only); a running app just ignores the second start.
    The browser runs native hosts in a job object: break away so the app outlives this host."""
    exe = app_path()
    if not exe:
        return False
    base = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    for flags in (base | CREATE_BREAKAWAY_FROM_JOB, base):
        try:
            subprocess.Popen([exe, "--background"], cwd=os.path.dirname(exe), creationflags=flags, close_fds=True,
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


def main():
    # sendNativeMessage sends one message and closes stdin; connectNative keeps it open. Serve until EOF.
    while True:
        msg = read_message()
        if msg is None:
            break
        action = msg.get("action")
        if action == "launchApp":
            send({"ok": launch_app()})
        elif action == "ping":
            send({"ok": True})
        else:
            send({"ok": False, "error": "unknown action"})


if __name__ == "__main__":
    main()
