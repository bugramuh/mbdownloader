"""Download items, states, file categories and captured file types."""
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .i18n import _

# --- State ---

QUEUED, PROBING, DOWNLOADING, PAUSED, COMPLETED, FAILED, CANCELLED = (
    "queued", "probing", "downloading", "paused", "completed", "failed", "cancelled")
ACTIVE_STATES = {PROBING, DOWNLOADING}


def state_label(state: str) -> str:
    return {
        QUEUED: _("Queued"), PROBING: _("Connecting"), DOWNLOADING: _("Downloading"), PAUSED: _("Paused"),
        COMPLETED: _("Completed"), FAILED: _("Error"), CANCELLED: _("Cancelled"),
    }.get(state, state)


# --- Categories ---

CATEGORIES = ["image", "music", "video", "apps", "document", "compressed", "other"]

_CATEGORY_EXTS = {
    "image": {"jpg", "jpeg", "png", "gif", "webp", "heic", "heif", "bmp", "tiff", "tif", "svg", "psd", "ai", "raw", "cr2", "nef", "ico"},
    "music": {"mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "wma", "aiff", "alac"},
    "video": {"mp4", "m4v", "mkv", "webm", "mov", "avi", "flv", "wmv", "mpg", "mpeg", "ts", "m3u8", "3gp"},
    "apps": {"exe", "msi", "msix", "appx", "appxbundle", "msixbundle", "bat", "dmg", "pkg", "app", "apk", "ipa", "deb", "rpm", "appimage", "jar", "xpi", "crx"},
    "document": {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "txt", "rtf", "odt", "ods", "odp", "epub", "mobi", "csv", "md", "pages", "numbers", "key"},
    "compressed": {"zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "img", "cab"},
}


def category_label(cat: str) -> str:
    return {
        "image": _("Images"), "music": _("Music"), "video": _("Video"), "apps": _("Apps"),
        "document": _("Documents"), "compressed": _("Archives"), "other": _("Other"),
    }[cat]


def file_ext(name: str) -> str:
    base = os.path.basename(name)
    if "." not in base.strip("."):
        return ""
    return base.rsplit(".", 1)[1].lower()


def detect_category(file_name: str, kind: str) -> str:
    if kind == "video":
        return "video"
    ext = file_ext(file_name)
    if not ext:
        return "other"
    for cat, exts in _CATEGORY_EXTS.items():
        if ext in exts:
            return cat
    return "other"


# --- Captured file types ---

@dataclass(frozen=True)
class CaptureGroup:
    id: str
    category: str
    extensions: tuple


CAPTURE_GROUPS = [
    CaptureGroup("compressed", "compressed", ("zip", "rar", "7z", "tar", "gz", "tgz", "bz2", "xz", "zst", "iso", "img", "cab")),
    CaptureGroup("apps", "apps", ("exe", "msi", "msix", "appx", "dmg", "pkg", "apk", "ipa", "deb", "rpm", "appimage", "jar", "bin")),
    CaptureGroup("video", "video", ("mp4", "m4v", "mkv", "webm", "mov", "avi", "flv", "wmv", "mpg", "mpeg", "m3u8", "3gp")),
    CaptureGroup("music", "music", ("mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "wma", "aiff")),
    CaptureGroup("document", "document", ("pdf", "epub", "mobi", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "odt", "ods", "odp", "rtf", "csv")),
    CaptureGroup("image", "image", ("jpg", "jpeg", "png", "gif", "webp", "heic", "bmp", "tiff", "psd", "svg", "raw")),
]

DEFAULT_CAPTURE_GROUPS = {"compressed", "apps", "video", "music", "document"}


def capture_extensions(group_ids: set, custom: str) -> list[str]:
    out: list[str] = []
    for g in CAPTURE_GROUPS:
        if g.id in group_ids:
            out += list(g.extensions)
    for token in re.split(r"[,\s;]+", custom.lower()):
        e = token.strip(". ")
        if e and e not in out:
            out.append(e)
    return out


def url_matches_extensions(url: str, extensions: list[str]) -> bool:
    try:
        path = urlsplit(url).path
    except ValueError:
        return False
    ext = file_ext(path)
    return bool(ext) and ext in extensions


# --- Items ---

@dataclass
class SegmentInfo:
    start: int
    end: int            # inclusive
    received: int = 0

    @property
    def length(self) -> int:
        return self.end - self.start + 1

    @property
    def done(self) -> bool:
        return self.received >= self.length

    def copy(self) -> "SegmentInfo":
        return SegmentInfo(self.start, self.end, self.received)


_PERSISTED = ("id", "url", "fileName", "directory", "kind", "state", "totalBytes", "receivedBytes", "supportsRanges",
              "headers", "cookieFile", "errorMessage", "createdAt", "completedAt", "finalPath", "requestedSegments", "sourceBrowser")


@dataclass
class DownloadItem:
    url: str
    fileName: str
    directory: str
    kind: str = "file"                  # file | video
    id: str = field(default_factory=lambda: str(uuid.uuid4()).upper())
    state: str = QUEUED
    totalBytes: int = -1
    receivedBytes: int = 0
    supportsRanges: bool = False
    segments: list = field(default_factory=list)
    headers: dict = field(default_factory=dict)
    cookieFile: str | None = None
    errorMessage: str | None = None
    createdAt: float = field(default_factory=time.time)
    completedAt: float | None = None
    finalPath: str | None = None
    requestedSegments: int = 0          # 0 = value from settings
    sourceBrowser: str | None = None    # extension that sent it (chrome, edge, brave, opera, firefox)
    # Not persisted
    speed: float = 0.0
    log: str = ""
    stage: str = ""                     # yt-dlp: current phase (downloading video/audio, merging…)
    lastHTTPStatus: int | None = None

    @property
    def part_path(self) -> str:
        """Partial file: id based so it stays put when the name changes."""
        return os.path.join(self.directory, f".dm-{self.id}.part")

    @property
    def progress(self) -> float:
        if self.totalBytes <= 0:
            return 0.0
        return min(1.0, self.receivedBytes / self.totalBytes)

    @property
    def eta(self) -> float | None:
        if self.totalBytes <= 0 or self.speed <= 1:
            return None
        return (self.totalBytes - self.receivedBytes) / self.speed

    @property
    def category(self) -> str:
        return detect_category(self.fileName, self.kind)

    @property
    def is_finished(self) -> bool:
        return self.state == COMPLETED

    @property
    def is_active(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def referer(self) -> str | None:
        return self.headers.get("Referer") or None

    def to_json(self) -> dict:
        d = {k: getattr(self, k) for k in _PERSISTED}
        d["segments"] = [{"start": s.start, "end": s.end, "received": s.received} for s in self.segments]
        return d

    @classmethod
    def from_json(cls, d: dict) -> "DownloadItem":
        kw = {k: d[k] for k in _PERSISTED if k in d}
        item = cls(**kw)
        item.segments = [SegmentInfo(int(s["start"]), int(s["end"]), int(s.get("received", 0))) for s in d.get("segments", [])]
        return item
