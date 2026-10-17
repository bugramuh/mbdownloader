"""Checks GitHub Releases for a newer Windows build and offers to download it.

Only releases that carry a Windows asset (.exe, .msi or a *win*.zip) are offered, so a macOS-only release of
the upstream project never triggers a prompt.
"""
import json
import re
import threading
import time

from PySide6.QtCore import QObject, QTimer, Signal

from . import paths, winshell
from .downloader import describe_error, pool
from .i18n import _
from .qtutil import ask, on_main

REPO = ""   # set to "owner/repo" to enable update checks against GitHub Releases
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"


def is_newer(a: str, b: str) -> bool:
    def parts(v):
        return [int(re.match(r"\d*", p).group() or 0) for p in v.split(".")]
    pa, pb = parts(a), parts(b)
    for i in range(max(len(pa), len(pb))):
        x = pa[i] if i < len(pa) else 0
        y = pb[i] if i < len(pb) else 0
        if x != y:
            return x > y
    return False


def windows_asset(assets: list) -> str | None:
    for a in assets:
        name = str(a.get("name", "")).lower()
        if name.endswith((".exe", ".msi")) or (name.endswith(".zip") and "win" in name):
            return a.get("browser_download_url")
    return None


class UpdateChecker(QObject):
    changed = Signal()

    def __init__(self, store, settings):
        super().__init__()
        self.store = store
        self.settings = settings
        self.latest_version: str | None = None
        self.checking = False
        self.last_check: float | None = None
        self.last_error: str | None = None
        QTimer.singleShot(8000, self._check_if_due)
        self._timer = QTimer(self, interval=3600_000)
        self._timer.timeout.connect(self._check_if_due)
        self._timer.start()

    def _check_if_due(self):
        if not REPO or not self.settings.checkForUpdates:
            return
        if time.time() - self.settings.lastUpdateCheck > 20 * 3600:
            self.check(interactive=False)

    def check(self, interactive: bool):
        if self.checking:
            return
        if not REPO:
            self.last_error = _("No update source is configured.")
            self.changed.emit()
            if interactive:
                self._show_no_update(self.last_error)
            return
        self.checking = True
        self.last_error = None
        self.changed.emit()

        def work():
            data, err = None, None
            try:
                r = pool().request("GET", f"https://api.github.com/repos/{REPO}/releases/latest", timeout=15,
                                   headers={"Accept": "application/vnd.github+json", "User-Agent": "MBDownloader"})
                if r.status == 200:
                    data = json.loads(r.data.decode("utf-8"))
                else:
                    err = _("No release information available yet.")
            except Exception as e:  # noqa: BLE001
                err = describe_error(e)
            on_main(lambda: self._done(data, err, interactive))
        threading.Thread(target=work, daemon=True).start()

    def _done(self, data, err, interactive):
        self.checking = False
        self.last_check = time.time()
        self.settings.lastUpdateCheck = self.last_check
        if not isinstance(data, dict) or "tag_name" not in data:
            self.last_error = err or _("No release information available yet.")
            self.changed.emit()
            if interactive:
                self._show_no_update(self.last_error)
            return
        tag = str(data["tag_name"])
        version = tag[1:] if tag.startswith("v") else tag
        asset = windows_asset(data.get("assets") or [])
        self.latest_version = version
        self.changed.emit()
        if asset and is_newer(version, paths.VERSION):
            if interactive or self.settings.skippedVersion != version:
                self._show_update(version, asset, str(data.get("html_url") or RELEASES_PAGE), str(data.get("body") or "").strip())
        elif interactive:
            self._show_no_update(None)

    def _show_update(self, version, asset_url, page, notes):
        info = _("You are using version {0}.", paths.VERSION)
        if notes:
            info += "\n\n" + notes[:600]
        idx, _cb = ask(None, paths.APP_NAME, _("MBDownloader {0} is available", version), info,
                       [_("Download Update"), _("Later"), _("Skip This Version")], escape_index=1)
        if idx == 0:
            from .store import NewDownloadRequest
            self.store.add(NewDownloadRequest(url=asset_url, fileName=asset_url.rsplit("/", 1)[-1]))
        elif idx == 2:
            self.settings.skippedVersion = version

    def _show_no_update(self, error):
        if error:
            ask(None, paths.APP_NAME, _("Could not check for updates"), error, ["OK"])
        else:
            ask(None, paths.APP_NAME, _("You're up to date"),
                _("MBDownloader {0} is the latest version.", paths.VERSION), ["OK"])
