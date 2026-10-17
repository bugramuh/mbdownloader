"""File-system locations: the app folder, bundled resources and %APPDATA% support folder."""
import ctypes
import os
import sys
import uuid
from ctypes import wintypes

APP_NAME = "MBDownloader"
VERSION = "1.4.0"
EXE_NAME = "MBDownloader.exe"
HOST_EXE_NAME = "mbdownloader-native-host.exe"

FROZEN = getattr(sys, "frozen", False)


def package_dir() -> str:
    """Folder that holds the mbdownloader package (inside the PyInstaller bundle when frozen)."""
    if FROZEN:
        return os.path.join(sys._MEIPASS, "mbdownloader")  # type: ignore[attr-defined]
    return os.path.dirname(os.path.abspath(__file__))


def app_dir() -> str:
    """Folder of the installed app (MBDownloader.exe, tools\\, Extension\\, mbdownloader-native-host.exe)."""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(package_dir())


def resource_path(*parts: str) -> str:
    return os.path.join(package_dir(), "resources", *parts)


def _known_folder(guid: str) -> str | None:
    class GUID(ctypes.Structure):
        _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    try:
        u = uuid.UUID(guid)
        g = GUID(u.fields[0], u.fields[1], u.fields[2], (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
            return None
        path = out.value
        ctypes.windll.ole32.CoTaskMemFree(out)
        return path
    except Exception:
        return None


def downloads_dir() -> str:
    return _known_folder("{374DE290-123F-4565-9164-39C4925E467B}") or os.path.join(os.path.expanduser("~"), "Downloads")


def support_dir() -> str:
    """%APPDATA%\\MBDownloader (settings, download list, cookies, user-installed tools)."""
    base = os.environ.get("APPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    d = os.path.join(base, APP_NAME)
    os.makedirs(d, exist_ok=True)
    return d


def user_bin_dir() -> str:
    return os.path.join(support_dir(), "bin")


def bundled_tools_dir() -> str:
    return os.path.join(app_dir(), "tools")


def default_download_dir() -> str:
    return os.path.join(downloads_dir(), APP_NAME)


def bundled_extension_dir() -> str:
    override = os.environ.get("MBD_EXTENSION_DIR")
    if override:
        return override
    return os.path.join(app_dir(), "Extension")
