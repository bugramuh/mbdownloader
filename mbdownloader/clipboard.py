"""Watches the clipboard for http(s) links and asks whether to download them. Also used by Ctrl+V in the main window."""
import re
from urllib.parse import urlsplit

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QMessageBox

from . import paths
from .i18n import _
from .models import detect_category, url_matches_extensions
from .qtutil import ask

VIDEO_HOSTS = ["youtube.com", "youtu.be", "vimeo.com", "dailymotion.com", "twitter.com", "x.com", "instagram.com",
               "tiktok.com", "twitch.tv", "facebook.com", "reddit.com", "bilibili.com"]


def _is_http_url(s: str) -> bool:
    try:
        p = urlsplit(s)
    except ValueError:
        return False
    return p.scheme.lower() in ("http", "https") and bool(p.hostname)


def url_only_content(text: str) -> list[str] | None:
    """The links if every non-empty line of `text` is a single http(s) URL; None otherwise."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if not lines or len(lines) > 50:
        return None
    urls: list[str] = []
    for line in lines:
        if " " in line or not _is_http_url(line):
            return None
        if line not in urls:
            urls.append(line)
    return urls


def extract_urls(text: str) -> list[str]:
    out: list[str] = []
    for token in re.split(r"\s+", text):
        s = token.strip("<>\"'()[]{},;")
        if _is_http_url(s) and s not in out:
            out.append(s)
    return out


def looks_like_video_page(url: str) -> bool:
    p = urlsplit(url)
    host = (p.hostname or "").lower()
    if detect_category(p.path.rsplit("/", 1)[-1], "file") != "other":
        return False
    return any(host == h or host.endswith("." + h) for h in VIDEO_HOSTS)


class ClipboardWatcher(QObject):
    def __init__(self, store, settings):
        super().__init__()
        self.store = store
        self.settings = settings
        self._asked: list[str] = []
        self._prompting = False
        self._last_text = QApplication.clipboard().text()
        QApplication.clipboard().dataChanged.connect(lambda: QTimer.singleShot(150, self._changed))

    def _changed(self):
        if not self.settings.watchClipboard or self._prompting:
            return
        text = QApplication.clipboard().text()
        if not text or text == self._last_text:
            return
        self._last_text = text
        # Only react when the clipboard holds nothing but links, and the links point at files of a captured type.
        urls = url_only_content(text)
        if not urls:
            return
        exts = self.settings.capture_extensions
        urls = [u for u in urls if url_matches_extensions(u, exts) and u not in self._asked]
        if urls:
            self._remember(urls)
            self._ask(urls, clipboard=True)

    def handle_paste(self, text: str):
        """Called from Ctrl+V in the main window (explicit user action: any http(s) link is accepted)."""
        urls = url_only_content(text)
        if urls:
            self._remember(urls)
            self._ask(urls, clipboard=False)

    def _remember(self, urls):
        self._asked += urls
        del self._asked[:-50]

    def _ask(self, urls: list[str], clipboard: bool):
        self._prompting = True
        n = len(urls)
        if n == 1:
            head = _("A link was copied. Download it?") if clipboard else _("Download the pasted link?")
            info = urls[0]
        else:
            head = _("{0} links were copied. Download them?", n) if clipboard else _("Download the {0} pasted links?", n)
            info = "\n".join(urls[:6]) + ("\n…" if n > 6 else "")
        prefer_video = all(looks_like_video_page(u) for u in urls)
        if prefer_video:
            buttons = [_("Download Video (yt-dlp)"), _("Download as File"), _("Ignore")]
        else:
            buttons = [_("Download"), _("Download Video (yt-dlp)"), _("Ignore")]
        idx, stop = ask(None, paths.APP_NAME, head, info, buttons,
                        checkbox=_("Stop watching the clipboard") if clipboard else None, escape_index=2)
        self._prompting = False
        self._last_text = QApplication.clipboard().text()
        if stop:
            self.settings.watchClipboard = False
        if idx == 0:
            kind = "video" if prefer_video else "file"
        elif idx == 1:
            kind = "file" if prefer_video else "video"
        else:
            return
        from .store import NewDownloadRequest
        for u in urls:
            self.store.add(NewDownloadRequest(url=u, kind=kind))
