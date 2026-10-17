"""Registers the native messaging host (mbdownloader-native-host.exe) with Chromium browsers and Firefox, so their
extension can start the app when a download arrives while it is not running.

On Windows each browser looks the host up in the registry (HKCU\\Software\\<vendor>\\NativeMessagingHosts\\<name>),
whose default value is the path of a JSON manifest naming the host binary and the extensions allowed to call it.
Rewritten at every launch, so moving the app folder keeps the path current.
"""
import hashlib
import json
import os
import threading
import winreg

from . import browsers, paths

HOST_NAME = "com.mbdownloader.host"
FIREFOX_ADDON_ID = "mbdownloader@mbdownloader.app"

CHROMIUM_KEYS = [
    r"Software\Google\Chrome\NativeMessagingHosts",          # Chrome; also read by Opera and Vivaldi
    r"Software\Microsoft\Edge\NativeMessagingHosts",
    r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts",
    r"Software\Chromium\NativeMessagingHosts",
]
FIREFOX_KEY = r"Software\Mozilla\NativeMessagingHosts"

_lock = threading.Lock()


def host_binary() -> str:
    return os.path.join(paths.app_dir(), paths.HOST_EXE_NAME)


def unpacked_extension_id(path: str) -> str:
    """An unpacked extension without a "key" gets its id from its folder path. On Windows Chromium hashes the
    UTF-16LE path (drive letter upper-cased): first 128 bits of SHA-256, hex digits mapped 0-f → a-p."""
    p = os.path.normpath(path)
    if len(p) >= 2 and p[1] == ":":
        p = p[0].upper() + p[1:]
    digest = hashlib.sha256(p.encode("utf-16-le")).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def is_valid_chromium_id(id_: str) -> bool:
    return len(id_) == 32 and all("a" <= c <= "p" for c in id_)


def allowed_chromium_ids(settings) -> list[str]:
    ids = set(settings.chromiumExtensionIDs)
    for kind in browsers.CHROMIUM_KINDS:
        ids.add(unpacked_extension_id(browsers.plugin_folder(kind)))
    return sorted(ids)


def note_extension_id(settings, id_: str):
    """Called by the bridge with the id of a Chromium extension that talked to it; re-registers when new."""
    if not is_valid_chromium_id(id_) or id_ in settings.chromiumExtensionIDs:
        return
    settings.chromiumExtensionIDs = sorted(set(settings.chromiumExtensionIDs) | {id_})
    register_async(settings)


def register_async(settings):
    ids = allowed_chromium_ids(settings)
    threading.Thread(target=register, args=(ids,), daemon=True).start()


def _write_json(path: str, obj: dict):
    data = json.dumps(obj, indent=2, sort_keys=True).encode("utf-8")
    try:
        with open(path, "rb") as f:
            if f.read() == data:
                return
    except OSError:
        pass
    with open(path, "wb") as f:
        f.write(data)


def _set_default(key_path: str, value: str):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key_path}\{HOST_NAME}") as k:
        try:
            if winreg.QueryValueEx(k, "")[0] == value:
                return
        except OSError:
            pass
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, value)


def register(chromium_ids: list[str]):
    """Writes both manifests and the registry entries. Skipped when the app carries no host binary (source checkout)."""
    exe = host_binary()
    if not os.path.isfile(exe):
        return
    with _lock:
        d = os.path.join(paths.support_dir(), "native-host")
        os.makedirs(d, exist_ok=True)
        chromium_manifest = os.path.join(d, f"{HOST_NAME}.chromium.json")
        firefox_manifest = os.path.join(d, f"{HOST_NAME}.firefox.json")
        base = {"name": HOST_NAME, "description": "MBDownloader launcher", "path": exe, "type": "stdio"}
        try:
            _write_json(chromium_manifest, {**base, "allowed_origins": [f"chrome-extension://{i}/" for i in chromium_ids]})
            _write_json(firefox_manifest, {**base, "allowed_extensions": [FIREFOX_ADDON_ID]})
            for key in CHROMIUM_KEYS:
                _set_default(key, chromium_manifest)
            _set_default(FIREFOX_KEY, firefox_manifest)
        except OSError:
            pass
