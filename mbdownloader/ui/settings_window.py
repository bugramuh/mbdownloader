"""Settings window: General, File Types, Browser Extensions, Video."""
import os

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit,
                               QProgressBar, QPushButton, QScrollArea, QSlider, QSpinBox, QTabWidget, QVBoxLayout, QWidget)

from .. import browsers, cookies, fmt, paths, qtutil, tools, video, winshell
from ..i18n import _
from .dialogs import CaptureTypesEditor
from .widgets import hint_label


def page(*widgets) -> QScrollArea:
    inner = QWidget()
    lay = QVBoxLayout(inner)
    lay.setSpacing(10)
    for w in widgets:
        lay.addWidget(w)
    lay.addStretch(1)
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setFrameShape(QScrollArea.NoFrame)
    sa.setWidget(inner)
    return sa


def group(title: str) -> tuple[QGroupBox, QVBoxLayout]:
    g = QGroupBox(title)
    lay = QVBoxLayout(g)
    return g, lay


def check(settings, key: str, text: str, after=None) -> QCheckBox:
    cb = QCheckBox(text)
    cb.setChecked(bool(getattr(settings, key)))

    def toggled(on):
        setattr(settings, key, on)
        if after:
            after(on)
    cb.toggled.connect(toggled)
    return cb


def row(*widgets, stretch_first=False) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for i, x in enumerate(widgets):
        lay.addWidget(x, 1 if (stretch_first and i == 0) else 0)
    return w


class GeneralTab(QScrollArea):
    def __init__(self, ctx):
        super().__init__()
        s = ctx.settings
        self.ctx = ctx

        g1, l1 = group(_("Downloads"))
        self.dir_label = QLineEdit(s.downloadDirectory)
        self.dir_label.setReadOnly(True)
        show = QPushButton(_("Show"))
        show.clicked.connect(lambda: winshell.reveal(s.downloadDirectory))
        choose = QPushButton(_("Choose…"))
        choose.clicked.connect(self._choose)
        l1.addWidget(QLabel(_("Download folder")))
        l1.addWidget(row(self.dir_label, show, choose, stretch_first=True))
        l1.addWidget(check(s, "organizeByCategory", _("Organize downloads into category subfolders (Video, Music, Archives…)")))

        g2, l2 = group(_("Connections"))
        self.conn_label = QLabel()
        sl = QSlider(Qt.Horizontal)
        sl.setRange(1, 16)
        sl.setValue(s.segmentCount)
        sl.valueChanged.connect(lambda v: (setattr(s, "segmentCount", v), self._upd(v)))
        self._upd(s.segmentCount)
        l2.addWidget(self.conn_label)
        l2.addWidget(sl)
        l2.addWidget(hint_label(_("Each file is downloaded with up to this many parallel connections (4–10 recommended).")))
        sp = QSpinBox()
        sp.setRange(1, 10)
        sp.setValue(s.maxConcurrent)
        sp.valueChanged.connect(lambda v: setattr(s, "maxConcurrent", v))
        l2.addWidget(row(QLabel(_("Concurrent downloads:")), sp))

        g3, l3 = group(_("Behaviour"))
        for key, text in (("autoStart", _("Start new downloads immediately")),
                          ("notifyOnComplete", _("Show a notification when a download completes")),
                          ("showDetailOnStart", _("Open the download window when a download starts")),
                          ("autoCloseDetail", _("Close the download window automatically when completed")),
                          ("watchClipboard", _("Watch the clipboard for links and ask to download them"))):
            l3.addWidget(check(s, key, text))
        l3.addWidget(hint_label(_("Only links that point to a captured file type trigger the prompt. You can also paste a link into the main window with Ctrl+V.")))

        g4, l4 = group(_("System tray"))
        self.login = QCheckBox(_("Open at login"))
        self.login.setChecked(winshell.login_item_enabled())
        self.login.toggled.connect(self._toggle_login)
        self.login_err = QLabel()
        self.login_err.setStyleSheet("color:#d1242f")
        self.login_err.hide()
        l4.addWidget(self.login)
        l4.addWidget(self.login_err)
        l4.addWidget(check(s, "startInTray", _("Start in the system tray without opening the main window")))
        l4.addWidget(hint_label(_("The app keeps running in the system tray so browser extensions can hand over downloads at any time. Closing the window does not quit; use Quit from the tray icon.")))
        if not paths.FROZEN:
            l4.addWidget(hint_label(_("Open at login is only available in the packaged app.")))
            self.login.setEnabled(False)

        g5, l5 = group(_("Updates"))
        l5.addWidget(check(s, "checkForUpdates", _("Check for updates automatically (GitHub Releases)")))
        up = ctx.updater
        self.ver = QLabel()
        self.check_btn = QPushButton()
        self.check_btn.clicked.connect(lambda: up.check(interactive=True))
        l5.addWidget(row(self.ver, self.check_btn, stretch_first=True))
        up.changed.connect(self._upd_updates)
        self._upd_updates()

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        for g in (g1, g2, g3, g4, g5):
            lay.addWidget(g)
        lay.addStretch(1)
        self.setWidget(inner)

    def _upd(self, v):
        self.conn_label.setText(_("Connections per download: {0}", v))

    def _upd_updates(self):
        up = self.ctx.updater
        lines = [_("Current version: {0}", paths.VERSION)]
        if up.latest_version:
            lines.append(_("Latest release: {0}", up.latest_version))
        if up.last_check:
            lines.append(_("Last checked: {0}", fmt.date_time(up.last_check)))
        if up.last_error:
            lines.append(up.last_error)
        self.ver.setText("\n".join(lines))
        self.check_btn.setText(_("Checking…") if up.checking else _("Check Now"))
        self.check_btn.setEnabled(not up.checking)

    def _choose(self):
        d = QFileDialog.getExistingDirectory(self, _("Download folder"), self.ctx.settings.downloadDirectory)
        if d:
            d = os.path.normpath(d)
            self.ctx.settings.downloadDirectory = d
            self.dir_label.setText(d)

    def _toggle_login(self, on):
        try:
            winshell.set_login_item(on)
            self.login_err.hide()
        except OSError as e:
            self.login_err.setText(str(e))
            self.login_err.show()
            self.login.blockSignals(True)
            self.login.setChecked(winshell.login_item_enabled())
            self.login.blockSignals(False)


class BrowserTab(QScrollArea):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        s = ctx.settings
        g1, l1 = group(_("Browser extensions"))
        l1.addWidget(hint_label(_("The guided setup walks you through each installed browser one at a time: Google Chrome, Microsoft Edge, Brave and Opera (loaded from a folder), Firefox (signed add-on, one click).")))
        wiz = QPushButton(qtutil.glyph_icon("wand"), _("Open Setup Wizard…"))
        wiz.clicked.connect(ctx.show_wizard)
        l1.addWidget(row(wiz, QPushButton(_("Refresh Status"), clicked=self.refresh)))
        self.status_box = QVBoxLayout()
        l1.addLayout(self.status_box)
        folder = QLineEdit(browsers.plugins_root())
        folder.setReadOnly(True)
        showf = QPushButton(_("Show Plugin Folder"))
        showf.clicked.connect(lambda: (os.makedirs(browsers.plugins_root(), exist_ok=True), winshell.reveal(browsers.plugins_root())))
        l1.addWidget(row(folder, showf, stretch_first=True))

        g2, l2 = group(_("Sites left to the browser"))
        l2.addWidget(hint_label(_("Downloads from these sites are not captured; the browser downloads them itself. Add a site from the extension's menu (“Leave this site's downloads to the browser”) or when a download fails.")))
        self.sites_box = QVBoxLayout()
        l2.addLayout(self.sites_box)

        g3, l3 = group(_("Local bridge"))
        port = QSpinBox()
        port.setRange(1024, 65535)
        port.setValue(s.serverPort)
        port.setFixedWidth(100)
        port.valueChanged.connect(lambda v: setattr(s, "serverPort", v))
        self.bridge = QLabel()
        self.bridge.setEnabled(False)
        l3.addWidget(row(QLabel(_("Port")), port, self.bridge))
        l3.addWidget(hint_label(_("The extension uses port 48213 by default. If you change it here, enter the same port in the extension settings.")))

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        for g in (g1, g2, g3):
            lay.addWidget(g)
        lay.addStretch(1)
        self.setWidget(inner)
        s.changed.connect(lambda k: k == "browserOnlySites" and self._fill_sites())
        ctx.server.status_changed.connect(self.refresh)
        self._timer = QTimer(self, interval=3000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    @staticmethod
    def _clear(layout):
        while layout.count():
            it = layout.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def refresh(self):
        self._clear(self.status_box)
        for k in browsers.installed_browsers():
            r = QHBoxLayout()
            r.addWidget(QLabel(browsers.NAMES[k]), 1)
            if self.ctx.server.is_connected(k, 3600):
                text, color = _("Connected"), "#2ea44f"
            elif browsers.files_prepared(k):
                text, color = _("Files prepared, not loaded yet"), ""
            else:
                text, color = _("Not installed"), ""
            lb = QLabel(text)
            if color:
                lb.setStyleSheet(f"color:{color}")
            else:
                lb.setEnabled(False)
            r.addWidget(lb)
            w = QWidget()
            w.setLayout(r)
            r.setContentsMargins(0, 0, 0, 0)
            self.status_box.addWidget(w)
        self.bridge.setText(self.ctx.server.status_text)
        self._fill_sites()

    def _fill_sites(self):
        self._clear(self.sites_box)
        sites = self.ctx.settings.browserOnlySites
        if not sites:
            lb = QLabel(_("No sites"))
            lb.setEnabled(False)
            self.sites_box.addWidget(lb)
        for site in sites:
            b = QPushButton(qtutil.glyph_icon("remove"), "")
            b.setFlat(True)
            b.setToolTip(_("Capture downloads from this site again"))
            b.clicked.connect(lambda _c=False, x=site: self.ctx.settings.set_browser_only(x, False))
            w = row(QLabel(site), b, stretch_first=True)
            self.sites_box.addWidget(w)


class VideoTab(QScrollArea):
    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        s = ctx.settings
        inst = tools.installer

        g1, l1 = group(_("Site login (YouTube age verification, members-only videos)"))
        l1.addWidget(hint_label(_("Some videos require a signed-in account. Sign in once in the built-in browser window; the saved cookies are passed to yt-dlp for video downloads. Downloads started from the browser extension already carry the browser's cookies.")))
        self.login_text = QLabel()
        sign = QPushButton(_("Sign in to YouTube…"))
        sign.clicked.connect(ctx.show_site_login)
        self.clear_btn = QPushButton(_("Clear"))
        self.clear_btn.clicked.connect(self._clear_login)
        l1.addWidget(row(self.login_text, sign, self.clear_btn, stretch_first=True))

        g2, l2 = group("yt-dlp")
        self.ytdlp_text = QLabel()
        self.ytdlp_btn = QPushButton()
        self.ytdlp_btn.clicked.connect(lambda: inst.install_ytdlp(self._after))
        l2.addWidget(row(self.ytdlp_text, self.ytdlp_btn, stretch_first=True))
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        l2.addWidget(self.progress)
        self.msg = hint_label("")
        l2.addWidget(self.msg)
        l2.addWidget(hint_label(_("“Install” downloads the latest yt-dlp release from GitHub into the application data folder (checksum verified). Update when YouTube downloads stop working.")))
        self.custom = QLineEdit(s.ytdlpPath)
        self.custom.setPlaceholderText(_("Custom yt-dlp path (empty = automatic)"))
        self.custom.editingFinished.connect(lambda: setattr(s, "ytdlpPath", self.custom.text().strip()))
        pick = QPushButton(_("Choose…"))
        pick.clicked.connect(self._pick)
        l2.addWidget(row(self.custom, pick, stretch_first=True))
        self.deno_text = QLabel()
        self.deno_btn = QPushButton()
        self.deno_btn.clicked.connect(lambda: inst.install_deno(self._after))
        l2.addWidget(row(self.deno_text, self.deno_btn, stretch_first=True))
        l2.addWidget(hint_label(_("YouTube requires a JavaScript runtime to unlock video streams. deno is downloaded from its official GitHub releases (checksum verified) into the application data folder.")))
        self.ff_text = QLabel()
        self.ff_btn = QPushButton()
        self.ff_btn.clicked.connect(lambda: inst.install_ffmpeg(self._after))
        l2.addWidget(row(self.ff_text, self.ff_btn, stretch_first=True))
        l2.addWidget(hint_label(_("ffmpeg merges video and audio streams and converts containers. It is downloaded from the BtbN FFmpeg-Builds releases ({0}, checksum verified).", tools.FFMPEG_SIZE)))

        g3, l3 = group(_("Default format"))
        form = QFormLayout()
        self.container = QComboBox()
        for label, val in ((_("MP4 (H.264/AAC, plays everywhere)"), "mp4"), ("MKV", "mkv"), ("WebM", "webm"), (_("Original (no conversion)"), "original")):
            self.container.addItem(label, val)
        self.container.setCurrentIndex(max(0, self.container.findData(s.videoContainer)))
        self.container.currentIndexChanged.connect(lambda _i: (setattr(s, "videoContainer", self.container.currentData()), self._args()))
        self.height = QComboBox()
        for label, val in ((_("Best available"), 0), ("2160p (4K)", 2160), ("1440p", 1440), ("1080p", 1080), ("720p", 720),
                           ("480p", 480), ("360p", 360), (_("Audio only (M4A)"), -1), (_("Audio only (MP3)"), -2)):
            self.height.addItem(label, val)
        self.height.setCurrentIndex(max(0, self.height.findData(s.videoMaxHeight)))
        self.height.currentIndexChanged.connect(lambda _i: (setattr(s, "videoMaxHeight", self.height.currentData()), self._args()))
        form.addRow(_("Container"), self.container)
        form.addRow(_("Maximum resolution"), self.height)
        l3.addLayout(form)
        l3.addWidget(hint_label(_("Default: MP4 at up to 1080p. Streams are merged with ffmpeg.")))
        self.fmt = QLineEdit(s.videoFormat)
        self.fmt.setPlaceholderText(_("Custom yt-dlp -f expression (overrides the pickers)"))
        self.fmt.editingFinished.connect(lambda: (setattr(s, "videoFormat", self.fmt.text()), self._args()))
        l3.addWidget(self.fmt)
        self.args = hint_label("")
        self.args.setTextInteractionFlags(Qt.TextSelectableByMouse)
        l3.addWidget(self.args)

        self.setWidgetResizable(True)
        self.setFrameShape(QScrollArea.NoFrame)
        inner = QWidget()
        lay = QVBoxLayout(inner)
        for g in (g1, g2, g3):
            lay.addWidget(g)
        lay.addStretch(1)
        self.setWidget(inner)
        inst.changed.connect(self.refresh)
        self.refresh()
        self._args()
        inst.refresh_versions(s.ytdlpPath)

    def _args(self):
        self.args.setText(_("Effective arguments: {0}", " ".join(self.ctx.settings.ytdlp_format_arguments)))

    def _after(self, _version, _err):
        tools.installer.refresh_versions(self.ctx.settings.ytdlpPath)

    def _pick(self):
        f, _f = QFileDialog.getOpenFileName(self, "yt-dlp", "", "yt-dlp (*.exe);;*")
        if f:
            self.custom.setText(os.path.normpath(f))
            self.ctx.settings.ytdlpPath = os.path.normpath(f)
            self.refresh()

    def _clear_login(self):
        self.ctx.clear_site_login()
        self.refresh()

    def show_refresh(self):
        self.refresh()

    def refresh(self):
        inst = tools.installer
        s = self.ctx.settings
        n, domains, saved = cookies.site_cookie_status()
        if n:
            text = _("Signed in: {0}", ", ".join(domains[:4]))
            if saved:
                text += "\n" + _("{0} cookies saved {1}", n, fmt.date_time(saved))
        else:
            text = _("Not signed in")
        self.login_text.setText(text)
        self.clear_btn.setEnabled(n > 0)

        y = video.locate_ytdlp(s.ytdlpPath)
        if y:
            self.ytdlp_text.setText(f"yt-dlp {inst.ytdlp_version or ''}\n{y}".strip())
        else:
            self.ytdlp_text.setText(_("yt-dlp not found"))
            self.ytdlp_text.setStyleSheet("color:#d1242f")
        if y:
            self.ytdlp_text.setStyleSheet("")
        self.ytdlp_btn.setText(_("Installing…") if inst.busy else (_("Install yt-dlp") if not y else _("Update yt-dlp")))
        d = video.locate_deno()
        self.deno_text.setText(f"{inst.deno_version or 'deno'}\n{d}" if d else _("deno not installed (needed for YouTube)"))
        self.deno_btn.setText(_("Install deno") if not d else _("Update deno"))
        f = video.locate_ffmpeg()
        self.ff_text.setText(f"{inst.ffmpeg_version or 'ffmpeg'}\n{f}" if f else _("ffmpeg not found (merging video and audio will not work)"))
        self.ff_text.setStyleSheet("" if f else "color:#d1242f")
        self.ff_btn.setText(_("Install ffmpeg") if not f else _("Update ffmpeg"))
        for b in (self.ytdlp_btn, self.deno_btn, self.ff_btn):
            b.setEnabled(not inst.busy)
        self.progress.setVisible(inst.busy)
        self.progress.setValue(int(inst.progress * 1000))
        self.msg.setVisible(bool(inst.message))
        self.msg.setText(inst.message or "")


class SettingsWindow(QWidget):
    def __init__(self, ctx):
        super().__init__()
        self.setWindowTitle(_("Settings"))
        self.setWindowIcon(qtutil.app_icon())
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        tabs.addTab(GeneralTab(ctx), qtutil.glyph_icon("settings"), _("General"))
        ft = QWidget()
        fl = QVBoxLayout(ft)
        g, gl = group(_("Captured file types"))
        gl.addWidget(hint_label(_("Browser downloads and copied links with these extensions are handed to the app. Everything else stays in the browser.")))
        gl.addWidget(CaptureTypesEditor(ctx.settings))
        fl.addWidget(g)
        fl.addStretch(1)
        tabs.addTab(ft, qtutil.glyph_icon("document"), _("File Types"))
        tabs.addTab(BrowserTab(ctx), qtutil.glyph_icon("puzzle"), _("Browser Extensions"))
        self.video_tab = VideoTab(ctx)
        tabs.addTab(self.video_tab, qtutil.glyph_icon("video"), _("Video"))
        lay.addWidget(tabs)
        self.setFixedSize(640, 600)
