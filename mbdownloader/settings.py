"""User preferences, stored as JSON in %APPDATA%\\MBDownloader\\settings.json."""
import json
import os

from PySide6.QtCore import QObject, Signal

from . import paths
from .models import CAPTURE_GROUPS, DEFAULT_CAPTURE_GROUPS, capture_extensions

DEFAULTS = {
    "segmentCount": 8,
    "maxConcurrent": 3,
    "serverPort": 48213,
    "downloadDirectory": "",          # filled with the default folder at load
    "ytdlpPath": "",
    "videoFormat": "",                # custom -f (overrides pickers when non-empty)
    "videoContainer": "mp4",          # mp4 | mkv | webm | original
    "videoMaxHeight": 1080,           # 0 = best, -1 = audio m4a, -2 = audio mp3
    "autoStart": True,
    "notifyOnComplete": True,
    "showDetailOnStart": True,
    "autoCloseDetail": False,
    "watchClipboard": True,
    "captureGroupIDs": sorted(DEFAULT_CAPTURE_GROUPS),
    "customExtensions": "",
    "onboardingDone": False,
    "organizeByCategory": True,
    "checkForUpdates": True,
    "startInTray": True,              # keep the main window closed at launch; reachable from the tray icon
    "loginItemAutoRegistered": False,
    "denoPromptSuppressed": False,
    "browserOnlySites": [],
    "chromiumExtensionIDs": [],
    "lastUpdateCheck": 0.0,
    "skippedVersion": "",
}


def site_key(host: str | None) -> str | None:
    if not host:
        return None
    h = host.strip().lower()
    if not h:
        return None
    if h.startswith("www."):
        h = h[4:]
    return h


class AppSettings(QObject):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self._file = os.path.join(paths.support_dir(), "settings.json")
        self._data = dict(DEFAULTS)
        try:
            with open(self._file, encoding="utf-8") as f:
                stored = json.load(f)
            for k, v in stored.items():
                if k not in DEFAULTS:
                    continue
                d = DEFAULTS[k]
                if isinstance(v, type(d)) or (isinstance(d, float) and isinstance(v, int)):
                    self._data[k] = v
        except (OSError, ValueError):
            pass
        if not self._data["downloadDirectory"]:
            self._data["downloadDirectory"] = paths.default_download_dir()
        known = {g.id for g in CAPTURE_GROUPS}
        self._data["captureGroupIDs"] = [g for g in self._data["captureGroupIDs"] if g in known]

    def get(self, key):
        return self._data[key]

    def set(self, key, value):
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._save()
        self.changed.emit(key)

    def _save(self):
        tmp = self._file + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self._file)
        except OSError:
            pass

    # --- Derived values ---

    @property
    def capture_extensions(self) -> list[str]:
        return capture_extensions(set(self._data["captureGroupIDs"]), self._data["customExtensions"])

    def set_browser_only(self, host: str | None, on: bool):
        key = site_key(host)
        if not key:
            return
        sites = list(self._data["browserOnlySites"])
        if on and key not in sites:
            sites.append(key)
            sites.sort()
        elif not on:
            sites = [s for s in sites if s != key]
        self.set("browserOnlySites", sites)

    @property
    def ytdlp_format_arguments(self) -> list[str]:
        container = self._data["videoContainer"]
        custom = self._data["videoFormat"].strip()
        if custom:
            return ["-f", custom, "--merge-output-format", "mkv" if container == "original" else container]
        mh = self._data["videoMaxHeight"]
        if mh == -1:
            return ["-f", "ba/b", "-x", "--audio-format", "m4a", "--audio-quality", "0"]
        if mh == -2:
            return ["-f", "ba/b", "-x", "--audio-format", "mp3", "--audio-quality", "0"]
        h = f"[height<={mh}]" if mh > 0 else ""
        args: list[str] = []
        if container == "mp4":
            # Prefer H.264 + AAC so the file plays everywhere (Windows Media Player, phones); else merge into mp4.
            expr = f"bv*{h}[vcodec^=avc1]+ba[acodec^=mp4a]/bv*{h}[ext=mp4]+ba[ext=m4a]/bv*{h}+ba/b{h}/b"
            args = ["--merge-output-format", "mp4", "--remux-video", "mp4"]
        elif container == "webm":
            expr = f"bv*{h}[ext=webm]+ba[ext=webm]/bv*{h}+ba/b{h}/b"
            args = ["--merge-output-format", "webm"]
        elif container == "mkv":
            expr = f"bv*{h}+ba/b{h}/b"
            args = ["--merge-output-format", "mkv"]
        else:
            expr = f"bv*{h}+ba/b{h}/b"
        return ["-f", expr] + args


def _make_property(key):
    return property(lambda self: self._data[key], lambda self, v: self.set(key, v))


for _k in DEFAULTS:
    setattr(AppSettings, _k, _make_property(_k))
