"""Windows shell helpers: Explorer, Recycle Bin, Mark of the Web, browsers, login item."""
import os
import subprocess
import sys
import winreg

from . import paths

CREATE_NO_WINDOW = 0x08000000


def reveal(path: str):
    """Opens Explorer with the file selected (or the folder itself)."""
    path = os.path.normpath(path)
    if os.path.isfile(path):
        subprocess.Popen(f'explorer /select,"{path}"')
    elif os.path.isdir(path):   # never create the folder: an empty plugin folder made Chrome report a missing manifest
        os.startfile(path)


def open_path(path: str):
    try:
        os.startfile(os.path.normpath(path))
    except OSError:
        pass


def open_url(url: str):
    try:
        os.startfile(url)
    except OSError:
        pass


def move_to_recycle_bin(path: str) -> bool:
    from PySide6.QtCore import QFile
    return QFile.moveToTrash(path)


def mark_of_the_web(path: str, source_url: str, referrer: str | None = None):
    """Tags a downloaded file like a browser download (Zone.Identifier), so SmartScreen assesses it on first open."""
    lines = ["[ZoneTransfer]", "ZoneId=3"]
    if referrer and referrer.lower().startswith(("http://", "https://")):
        lines.append("ReferrerUrl=" + referrer)
    if source_url.lower().startswith(("http://", "https://")):
        lines.append("HostUrl=" + source_url)
    try:
        with open(path + ":Zone.Identifier", "w", encoding="utf-8") as f:
            f.write("\r\n".join(lines) + "\r\n")
    except OSError:
        pass


# --- Browsers ---

def _app_path(exe: str) -> str | None:
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as k:
                v, _t = winreg.QueryValueEx(k, "")
                v = os.path.expandvars(v.strip('"'))
                if os.path.isfile(v):
                    return v
        except OSError:
            continue
    return None


_PF = os.environ.get("ProgramFiles", r"C:\Program Files")
_PF86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
_LAD = os.environ.get("LOCALAPPDATA", "")

BROWSER_EXES = {
    "chrome": ("chrome.exe", [rf"{_PF}\Google\Chrome\Application\chrome.exe", rf"{_PF86}\Google\Chrome\Application\chrome.exe",
                              rf"{_LAD}\Google\Chrome\Application\chrome.exe"]),
    "edge": ("msedge.exe", [rf"{_PF86}\Microsoft\Edge\Application\msedge.exe", rf"{_PF}\Microsoft\Edge\Application\msedge.exe"]),
    "brave": ("brave.exe", [rf"{_PF}\BraveSoftware\Brave-Browser\Application\brave.exe",
                            rf"{_LAD}\BraveSoftware\Brave-Browser\Application\brave.exe"]),
    "opera": ("opera.exe", [rf"{_LAD}\Programs\Opera\opera.exe", rf"{_LAD}\Programs\Opera\launcher.exe", rf"{_PF}\Opera\launcher.exe"]),
    "firefox": ("firefox.exe", [rf"{_PF}\Mozilla Firefox\firefox.exe", rf"{_PF86}\Mozilla Firefox\firefox.exe"]),
}


def browser_exe(kind: str) -> str | None:
    entry = BROWSER_EXES.get(kind)
    if not entry:
        return None
    exe, candidates = entry
    found = _app_path(exe)
    if found:
        return found
    for c in candidates:
        if c and os.path.isfile(c):
            return c
    return None


def open_in_browser(kind: str | None, target: str) -> bool:
    """Opens a URL (or a file such as an .xpi) in a specific browser; falls back to the default handler."""
    exe = browser_exe(kind) if kind else None
    if exe:
        try:
            subprocess.Popen([exe, target])
            return True
        except OSError:
            pass
    open_url(target)
    return False


# --- Open at login (HKCU\...\Run) ---

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_VALUE = paths.APP_NAME


def login_command() -> str:
    return f'"{sys.executable}" --background'


def login_item_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            v, _t = winreg.QueryValueEx(k, _RUN_VALUE)
            return bool(v)
    except OSError:
        return False


def set_login_item(on: bool):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
        if on:
            winreg.SetValueEx(k, _RUN_VALUE, 0, winreg.REG_SZ, login_command())
        else:
            try:
                winreg.DeleteValue(k, _RUN_VALUE)
            except OSError:
                pass


def register_login_item_by_default_if_needed(settings):
    """Enables 'Open at login' once after the first-run setup, so browser extensions find the bridge running
    after a reboot. Only for the packaged app (a source checkout would leave a stale entry)."""
    if not paths.FROZEN:
        return
    try:
        if login_item_enabled():
            set_login_item(True)    # keep the entry pointing at this copy when the app folder moved
        elif settings.onboardingDone and not settings.loginItemAutoRegistered:
            settings.loginItemAutoRegistered = True
            set_login_item(True)
    except OSError:
        pass
