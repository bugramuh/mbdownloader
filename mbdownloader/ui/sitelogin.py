"""In-app login window (QtWebEngine). Cookies from the session are exported to a Netscape cookie file that
yt-dlp uses, so age-restricted / members-only videos can be downloaded."""
import os

from PySide6.QtCore import QUrl
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .. import cookies, paths, qtutil
from ..i18n import _

START_URL = "https://accounts.google.com/ServiceLogin?continue=https://www.youtube.com/"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/141.0.0.0 Safari/537.36")


def available() -> bool:
    try:
        import PySide6.QtWebEngineWidgets  # noqa: F401
        return True
    except ImportError:
        return False


class SiteLoginWindow(QWidget):
    def __init__(self, on_saved=None):
        super().__init__()
        from PySide6.QtWebEngineCore import QWebEngineProfile
        from PySide6.QtWebEngineWidgets import QWebEngineView
        self.on_saved = on_saved
        self.setWindowTitle(_("Site Login"))
        self.setWindowIcon(qtutil.app_icon())
        self.resize(960, 700)
        storage = os.path.join(paths.support_dir(), "webview")
        self.profile = QWebEngineProfile("mbdownloader-login", self)
        self.profile.setPersistentStoragePath(storage)
        self.profile.setCachePath(os.path.join(storage, "cache"))
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        self.profile.setHttpUserAgent(UA)
        self._cookies: dict[tuple, object] = {}
        store = self.profile.cookieStore()
        store.cookieAdded.connect(self._added)
        store.cookieRemoved.connect(self._removed)
        store.loadAllCookies()

        self.view = QWebEngineView(self)
        from PySide6.QtWebEngineCore import QWebEnginePage
        self.view.setPage(QWebEnginePage(self.profile, self.view))
        self.view.urlChanged.connect(self._url_changed)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.view, 1)
        bar = QHBoxLayout()
        bar.setContentsMargins(16, 10, 16, 10)
        self.status = QLabel(_("Sign in to your account, then click “Save Cookies”."))
        self.status.setEnabled(False)
        bar.addWidget(self.status, 1)
        yt = QPushButton("YouTube")
        yt.clicked.connect(lambda: self.view.load(QUrl("https://www.youtube.com/")))
        save = QPushButton(_("Save Cookies"))
        save.setDefault(True)
        save.clicked.connect(self.save_cookies)
        bar.addWidget(yt)
        bar.addWidget(save)
        lay.addLayout(bar)

    def open(self, url: str = START_URL):
        self.view.load(QUrl(url))
        qtutil.bring_to_front(self)

    @staticmethod
    def _key(c):
        return (bytes(c.domain().encode() if isinstance(c.domain(), str) else c.domain()), c.path(), bytes(c.name()))

    def _added(self, c):
        self._cookies[self._key(c)] = c

    def _removed(self, c):
        self._cookies.pop(self._key(c), None)

    def _url_changed(self, url):
        if url.host():
            self.status.setText(_("Signed-in pages: {0}. Click “Save Cookies” when done.", url.host()))

    def save_cookies(self):
        rows = []
        for c in self._cookies.values():
            exp = c.expirationDate()
            rows.append({
                "name": bytes(c.name()).decode("utf-8", "replace"), "value": bytes(c.value()).decode("utf-8", "replace"),
                "domain": c.domain(), "path": c.path() or "/", "secure": c.isSecure(),
                "expirationDate": exp.toSecsSinceEpoch() if exp.isValid() and not c.isSessionCookie() else 0,
            })
        cookies.write_cookie_file(cookies.site_cookie_file(), rows)
        self.status.setText(_("Saved {0} cookies. yt-dlp will use them for video downloads.", len(rows)))
        if self.on_saved:
            self.on_saved()

    def clear(self):
        self.profile.cookieStore().deleteAllCookies()
        self._cookies.clear()
        try:
            os.remove(cookies.site_cookie_file())
        except OSError:
            pass
