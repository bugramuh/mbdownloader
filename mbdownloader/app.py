"""Application shell: single instance, system tray, startup flow."""
import ctypes
import getpass
import sys

from PySide6.QtCore import QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from . import clipboard as clipboard_mod
from . import fmt, i18n, native_host_registry, paths, qtutil, tools, winshell
from .i18n import _
from .server import ServerController
from .settings import AppSettings
from .store import DownloadStore
from .updater import UpdateChecker

APP_ID = "MBDownloader.App"


class AppContext:
    """Shared objects and window management."""

    def __init__(self, app: QApplication):
        self.app = app
        self.settings = AppSettings()
        tools.installer = tools.ToolInstaller()
        self.store = DownloadStore(self.settings)
        self.server = ServerController(self.store)
        self.updater = UpdateChecker(self.store, self.settings)
        self.clipboard = clipboard_mod.ClipboardWatcher(self.store, self.settings)
        self._main = self._settings = self._wizard = self._login = self._detail = None
        self.store.show_detail.connect(lambda id_: self.detail.show_download(id_))
        self.store.notify.connect(self._notify)
        self.tray: QSystemTrayIcon | None = None

    # --- Windows (created lazily) ---

    @property
    def main(self):
        if self._main is None:
            from .ui.main_window import MainWindow
            self._main = MainWindow(self)
        return self._main

    @property
    def detail(self):
        if self._detail is None:
            from .ui.detail import DetailWindow
            self._detail = DetailWindow(self)
        return self._detail

    def show_main(self):
        qtutil.bring_to_front(self.main)

    def show_add(self):
        self.show_main()
        QTimer.singleShot(150, self.main.show_add_dialog)

    def show_settings(self):
        if self._settings is None:
            from .ui.settings_window import SettingsWindow
            self._settings = SettingsWindow(self)
        qtutil.bring_to_front(self._settings)

    def show_wizard(self):
        if self._wizard is None:
            from .ui.wizard import ExtensionWizard
            self._wizard = ExtensionWizard(self)
        else:
            self._wizard.reset()
        qtutil.bring_to_front(self._wizard)

    def show_site_login(self):
        from .ui import sitelogin
        if not sitelogin.available():
            qtutil.ask(None, paths.APP_NAME, _("Site login is not available"),
                       _("This build does not include the embedded browser (QtWebEngine)."), ["OK"])
            return
        if self._login is None:
            self._login = sitelogin.SiteLoginWindow(on_saved=self._login_saved)
        self._login.open()

    def _login_saved(self):
        if self._settings:
            self._settings.video_tab.refresh()

    def clear_site_login(self):
        from .ui import sitelogin
        if self._login is not None:
            self._login.clear()
        else:
            from . import cookies
            try:
                import os
                os.remove(cookies.site_cookie_file())
            except OSError:
                pass

    def _notify(self, title, body):
        if self.tray and self.tray.supportsMessages():
            self.tray.showMessage(title, body, qtutil.app_icon(), 6000)

    def quit(self):
        self.store.shutdown()
        self.server.stop()
        if self.tray:
            self.tray.hide()
        self.app.quit()

    # --- Tray ---

    def setup_tray(self):
        self.tray = QSystemTrayIcon(qtutil.app_icon(), self.app)
        menu = QMenu()
        self.tray_status = QAction(menu)
        self.tray_status.setEnabled(False)
        self.tray_bridge = QAction(menu)
        self.tray_bridge.setEnabled(False)
        menu.addAction(self.tray_status)
        menu.addAction(self.tray_bridge)
        menu.addSeparator()
        menu.addAction(_("Show Window"), self.show_main)
        menu.addAction(_("New Download…"), self.show_add)
        menu.addSeparator()
        menu.addAction(_("Pause All"), self.store.pause_all)
        menu.addAction(_("Resume All"), self.store.resume_all)
        menu.addSeparator()
        menu.addAction(_("Settings…"), self.show_settings)
        menu.addAction(_("Check for Updates…"), lambda: self.updater.check(interactive=True))
        menu.addSeparator()
        menu.addAction(_("Quit MBDownloader"), self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: r in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick) and self.show_main())
        menu.aboutToShow.connect(self.update_tray)
        self.store.changed.connect(self.update_tray)
        self.update_tray()
        self.tray.show()

    def update_tray(self):
        s = self.store
        if s.active_count > 0:
            p = s.overall_progress
            pct = f" · {int(p * 100)}%" if p is not None else ""
            self.tray_status.setText(_("{0} downloading · {1}", s.active_count, fmt.speed(s.total_speed)))
            tip = f"{paths.APP_NAME}{pct}\n{self.tray_status.text()}"
        else:
            self.tray_status.setText(_("No active downloads"))
            tip = paths.APP_NAME
        self.tray_bridge.setText(_("Bridge is running") if self.server.running else _("Bridge is not running"))
        if self.tray:
            self.tray.setToolTip(tip)


def _single_instance(server_name: str, show_existing: bool) -> QLocalServer | None:
    """Returns the listening server when this is the first instance; otherwise tells the running one to show
    its window (unless the launch was a background start) and returns None."""
    sock = QLocalSocket()
    sock.connectToServer(server_name)
    if sock.waitForConnected(300):
        if show_existing:
            sock.write(b"show")
            sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return None
    QLocalServer.removeServer(server_name)
    srv = QLocalServer()
    srv.listen(server_name)
    return srv


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    background = "--background" in argv
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass
    i18n.init()
    app = QApplication(argv)
    app.setApplicationName(paths.APP_NAME)
    app.setQuitOnLastWindowClosed(False)
    app.setWindowIcon(qtutil.app_icon())
    qtutil.init_invoker()

    server_name = f"MBDownloader-{getpass.getuser()}"
    instance = _single_instance(server_name, show_existing=not background)
    if instance is None:
        return 0

    ctx = AppContext(app)
    instance.newConnection.connect(lambda: (instance.nextPendingConnection(), ctx.show_main()))
    ctx.setup_tray()
    native_host_registry.register_async(ctx.settings)
    winshell.register_login_item_by_default_if_needed(ctx.settings)
    if ctx.settings.onboardingDone:
        ctx.store.prepare_download_folder()

    starts_hidden = ctx.settings.onboardingDone and (background or ctx.settings.startInTray)
    if not starts_hidden:
        ctx.show_main()
    if not ctx.settings.onboardingDone:
        from .ui.dialogs import OnboardingDialog
        QTimer.singleShot(300, lambda: OnboardingDialog(ctx, ctx.main).exec())
    app.aboutToQuit.connect(ctx.store.shutdown)
    return app.exec()
