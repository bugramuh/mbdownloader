"""Guided, one-browser-at-a-time setup of the browser extensions (Chrome, Edge, Brave, Opera, Firefox)."""
from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget, QFrame)

from .. import browsers, qtutil, winshell
from ..i18n import _
from .widgets import PathBox, StatusRow


class ExtensionWizard(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(_("Browser Extension Setup"))
        self.setWindowIcon(qtutil.app_icon())
        self.setFixedSize(640, 480)
        self.browsers = browsers.installed_browsers()
        self.index = 0
        self.step = 0
        self.prepared: dict[str, str] = {}
        self.copied = False

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(22, 14, 22, 14)
        self.head_icon = QLabel()
        self.head_icon.setFixedSize(40, 40)
        head.addWidget(self.head_icon)
        col = QVBoxLayout()
        self.title = QLabel()
        f = self.title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        self.title.setFont(f)
        self.sub = QLabel()
        self.sub.setEnabled(False)
        col.addWidget(self.title)
        col.addWidget(self.sub)
        head.addLayout(col, 1)
        self.dots = QLabel()
        head.addWidget(self.dots)
        root.addLayout(head)
        root.addWidget(self._line())

        self.body = QVBoxLayout()
        self.body.setContentsMargins(22, 18, 22, 18)
        self.body.setSpacing(12)
        holder = QWidget()
        holder.setLayout(self.body)
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QScrollArea.NoFrame)
        sa.setWidget(holder)
        root.addWidget(sa, 1)
        root.addWidget(self._line())

        foot = QHBoxLayout()
        foot.setContentsMargins(22, 12, 22, 12)
        self.back_btn = QPushButton(_("Back"))
        self.back_btn.clicked.connect(self._back)
        self.skip_btn = QPushButton(_("Skip This Browser"))
        self.skip_btn.clicked.connect(self._skip)
        self.next_btn = QPushButton()
        self.next_btn.setDefault(True)
        self.next_btn.clicked.connect(self._next)
        foot.addWidget(self.back_btn)
        foot.addStretch(1)
        foot.addWidget(self.skip_btn)
        foot.addWidget(self.next_btn)
        root.addLayout(foot)

        self._timer = QTimer(self, interval=1500)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._render()

    @staticmethod
    def _line():
        l = QFrame()
        l.setFrameShape(QFrame.HLine)
        l.setFrameShadow(QFrame.Sunken)
        return l

    @property
    def kind(self) -> str:
        return self.browsers[self.index] if self.browsers else "chrome"

    @property
    def step_count(self) -> int:
        return 2 if self.kind == "firefox" else 4

    @property
    def is_last_step(self):
        return self.step >= self.step_count - 1

    @property
    def is_last_browser(self):
        return self.index >= len(self.browsers) - 1

    def reset(self):
        self.browsers = browsers.installed_browsers()
        self.index = self.step = 0
        self._render()

    # --- Navigation ---

    def _next(self):
        self.copied = False
        if self.is_last_step:
            if self.is_last_browser:
                self.close()
                return
            self.index += 1
            self.step = 0
        else:
            self.step += 1
        self._render()

    def _back(self):
        if self.step > 0:
            self.step -= 1
        elif self.index > 0:
            self.index -= 1
            self.step = 0
        self._render()

    def _skip(self):
        if self.is_last_browser:
            self.close()
            return
        self.index += 1
        self.step = 0
        self._render()

    def _poll(self):
        if self.isVisible() and self._status_row is not None:
            self._update_status()

    # --- Rendering ---

    def _clear_body(self):
        while self.body.count():
            it = self.body.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._status_row = None

    def _add(self, w):
        self.body.addWidget(w)
        return w

    def _text(self, text, hint=False):
        lb = QLabel(text)
        lb.setWordWrap(True)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        if hint:
            lb.setEnabled(False)
        return self._add(lb)

    def _title(self, text):
        lb = QLabel(text)
        f = lb.font()
        f.setBold(True)
        f.setPointSize(f.pointSize() + 1)
        lb.setFont(f)
        self._add(lb)

    def _button(self, text, fn, icon=None, enabled=True):
        b = QPushButton(qtutil.glyph_icon(icon), text) if icon else QPushButton(text)
        b.setEnabled(enabled)
        b.clicked.connect(lambda _c=False: fn())
        return b

    def _buttons(self, *btns):
        r = QHBoxLayout()
        for b in btns:
            r.addWidget(b)
        r.addStretch(1)
        w = QWidget()
        w.setLayout(r)
        r.setContentsMargins(0, 0, 0, 0)
        self._add(w)

    def _render(self):
        self._clear_body()
        kind = self.kind
        name = browsers.NAMES.get(kind, kind)
        self.title.setText(name)
        self.sub.setText(_("Browser {0} of {1} · Step {2} of {3}", self.index + 1, max(1, len(self.browsers)), self.step + 1, self.step_count))
        self.dots.setText(" ".join("●" if i <= self.step else "○" for i in range(self.step_count)))
        self.head_icon.setPixmap(qtutil.glyph_pixmap("puzzle", 36))
        self.back_btn.setEnabled(not (self.step == 0 and self.index == 0))
        self.skip_btn.setVisible(not (self.is_last_browser and self.is_last_step))
        self.next_btn.setText(_("Finish") if self.is_last_step and self.is_last_browser else (_("Next Browser") if self.is_last_step else _("Next")))
        self.error = QLabel()
        self.error.setStyleSheet("color:#d1242f")
        self.error.setWordWrap(True)
        if not self.browsers:
            self._title(_("No supported browser was found"))
            self._text(_("Install Google Chrome, Microsoft Edge, Brave, Opera or Firefox, then open this setup again from Settings ▸ Browser Extensions."))
            return
        (self._firefox if kind == "firefox" else self._chromium)()
        self._add(self.error)
        self.body.addStretch(1)
        if self._status_row is not None:
            self._update_status()

    def _status(self, ok_text, pending_text, ok_fn):
        self._status_row = self._add(StatusRow())
        self._status_args = (ok_text, pending_text, ok_fn)
        self._update_status()

    def _update_status(self):
        ok_text, pending_text, ok_fn = self._status_args
        self._status_row.set_state(ok_fn(), ok_text, pending_text)

    def _chromium(self):
        kind, name = self.kind, browsers.NAMES[self.kind]
        folder = browsers.plugin_folder(kind)
        if not browsers.files_prepared(kind):   # never leave the user with a missing or empty folder
            try:
                browsers.prepare_files(kind)
            except OSError as e:
                self.error.setText(e.strerror or str(e))
        if self.step == 0:
            self._title(_("Prepare the extension files"))
            self._text(_("{0} does not allow apps to install extensions automatically, so the extension is loaded from a folder. The files are placed in:", name))
            self._add(PathBox(folder))
            self._buttons(self._button(_("Prepare Files"), self._prepare, "add"),
                          self._button(_("Show in Explorer"), lambda: winshell.reveal(folder), "folder", browsers.files_prepared(kind)))
            self._status(_("Files are ready"), _("Click “Prepare Files”"), lambda: browsers.files_prepared(kind))
            self._text(_("Keep this folder in place; the browser loads the extension from it."), hint=True)
        elif self.step == 1:
            self._title(_("Open the extensions page and turn on Developer mode"))
            where = _("in the left sidebar") if kind == "edge" else _("in the top right corner")
            self._text(_("Click the button below. On the page that opens, switch on “Developer mode” {0}.", where))
            self._buttons(self._button(_("Open {0}", browsers.EXTENSIONS_PAGES[kind]), lambda: browsers.open_extensions_page(kind), "globe"))
            if browsers.CHROME_WEB_STORE_URL:
                self._buttons(self._button(_("Or install from the Chrome Web Store"), lambda: winshell.open_url(browsers.CHROME_WEB_STORE_URL)))
        elif self.step == 2:
            self._title(_("Load the extension folder"))
            self._text(_("Click “Load unpacked” on the extensions page. In the folder dialog paste the folder path below into the address bar, press Enter, then click “Select Folder”."))
            self._add(PathBox(folder))
            self.copy_btn = self._button(_("Copied") if self.copied else _("Copy Folder Path"), self._copy_path, "copy")
            self._buttons(self.copy_btn, self._button(_("Show in Explorer"), lambda: winshell.reveal(folder), "folder"))
        else:
            self._title(_("{0} is ready", name))
            self._text(_("Pin MBDownloader to the toolbar (extensions icon ▸ pin). Its popup should show “Connected”. Downloads of the captured file types are now handed to the app."))
            self._status(_("Extension has connected to the app"), _("Waiting for the extension to connect (it connects right after loading)"),
                         lambda: self.ctx.server.is_connected(kind))

    def _firefox(self):
        if self.step == 0:
            if browsers.signed_firefox_addon():
                self._title(_("Add the extension to Firefox"))
                self._text(_("The Firefox add-on is included in the app and signed by Mozilla. Click the button below; Firefox opens and asks “Add MBDownloader?”. Click “Add”."))
                self._buttons(self._button(_("Add to Firefox"), browsers.install_firefox_addon, "puzzle"))
                self._text(_("To capture downloads in private windows too, tick “Allow this extension to run in Private Windows” in the confirmation that follows."), hint=True)
            elif browsers.unsigned_firefox_addon():
                self._title(_("Load the extension temporarily"))
                self._text(_("This build contains an unsigned Firefox add-on. Firefox only loads it temporarily: it is removed when Firefox quits. On the page that opens, click “Load Temporary Add-on…” and select the file below."))
                self._buttons(self._button(_("Open about:debugging"), self._open_debugging, "globe"))
                if "firefox" in self.prepared:
                    self._add(PathBox(self.prepared["firefox"]))
            else:
                self._title(_("Firefox add-on not included"))
                self._text(_("This build of the app does not contain the Firefox add-on. Build it with scripts\\pack-firefox.ps1, or install the release version of the app."))
        else:
            self._title(_("Firefox is ready"))
            self._text(_("Pin MBDownloader to the toolbar (puzzle icon ▸ gear ▸ Pin to Toolbar). Its popup should show “Connected”. Downloads of the captured file types are now handed to the app."))
            self._status(_("Extension has connected to the app"), _("Waiting for the extension to connect (it connects right after loading)"),
                         lambda: self.ctx.server.is_connected("firefox"))

    # --- Actions ---

    def _prepare(self):
        try:
            self.prepared[self.kind] = browsers.prepare_files(self.kind)
        except OSError as e:
            self._render()
            self.error.setText(e.strerror or str(e))
            return
        self._render()

    def _copy_path(self):
        QApplication.clipboard().setText(browsers.plugin_folder(self.kind))
        self.copied = True
        self.copy_btn.setText(_("Copied"))

    def _open_debugging(self):
        try:
            self.prepared["firefox"] = browsers.prepare_temporary_firefox_addon()
        except OSError as e:
            self.error.setText(e.strerror or str(e))
        browsers.open_firefox_debugging()
        self._render()

    def showEvent(self, e):
        super().showEvent(e)
