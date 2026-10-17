"""Add-download dialog, captured-file-types editor and the first-run onboarding."""
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
                               QPlainTextEdit, QPushButton, QSpinBox, QVBoxLayout, QWidget)

from .. import browsers, qtutil, winshell
from ..i18n import _
from ..models import CAPTURE_GROUPS, category_label
from .widgets import hint_label


class AddDownloadDialog(QDialog):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.setWindowTitle(_("New Download"))
        self.setWindowIcon(qtutil.app_icon())
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        title = QLabel(_("New Download"))
        f = title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        title.setFont(f)
        lay.addWidget(title)
        lay.addWidget(hint_label(_("One URL per line.")))
        self.text = QPlainTextEdit()
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.text.setFont(mono)
        self.text.setFixedHeight(120)
        lay.addWidget(self.text)
        self.video = QCheckBox(_("Download video from page (yt-dlp)"))
        lay.addWidget(self.video)
        row = QHBoxLayout()
        self.conn_label = QLabel()
        self.conn = QSpinBox()
        self.conn.setRange(0, 16)
        self.conn.setSpecialValueText(_("default ({0})", ctx.settings.segmentCount))
        row.addWidget(QLabel(_("Connections:")))
        row.addWidget(self.conn)
        row.addStretch(1)
        self.conn_widget = QWidget()
        self.conn_widget.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.conn_widget)
        self.video.toggled.connect(lambda on: self.conn_widget.setVisible(not on))

        buttons = QHBoxLayout()
        paste = QPushButton(_("Paste from Clipboard"))
        paste.clicked.connect(lambda: self.text.setPlainText(QApplication.clipboard().text()))
        buttons.addWidget(paste)
        buttons.addStretch(1)
        cancel = QPushButton(_("Cancel"))
        cancel.clicked.connect(self.reject)
        self.go = QPushButton(_("Download"))
        self.go.setDefault(True)
        self.go.clicked.connect(self._go)
        buttons.addWidget(cancel)
        buttons.addWidget(self.go)
        lay.addLayout(buttons)
        self.text.textChanged.connect(lambda: self.go.setEnabled(bool(self.text.toPlainText().strip())))
        self.go.setEnabled(False)

        clip = QApplication.clipboard().text().strip()
        if clip.lower().startswith("http") and "\n" not in clip:
            self.text.setPlainText(clip)
        self.text.setFocus()

    def _go(self):
        n = self.conn.value()
        self.ctx.store.add_urls(self.text.toPlainText(), "video" if self.video.isChecked() else "file", n or None)
        self.accept()


class CaptureTypesEditor(QWidget):
    """Category checkboxes + custom extensions. Used by the first-launch dialog and the Settings tab."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        for g in CAPTURE_GROUPS:
            cb = QCheckBox(f"{category_label(g.category)}   —   {', '.join(g.extensions)}")
            cb.setIcon(qtutil.glyph_icon(g.category))
            cb.setChecked(g.id in settings.captureGroupIDs)
            cb.toggled.connect(lambda on, gid=g.id: self._toggle(gid, on))
            lay.addWidget(cb)
        form = QFormLayout()
        self.custom = QLineEdit(settings.customExtensions)
        self.custom.setPlaceholderText(_("e.g. torrent, srt, ttf"))
        self.custom.editingFinished.connect(lambda: setattr(settings, "customExtensions", self.custom.text()))
        form.addRow(_("Other extensions"), self.custom)
        lay.addLayout(form)

    def _toggle(self, gid, on):
        ids = set(self.settings.captureGroupIDs)
        ids.add(gid) if on else ids.discard(gid)
        self.settings.captureGroupIDs = sorted(ids)


class OnboardingDialog(QDialog):
    """First-launch flow: 1) file types  2) browser extensions (opens the guided wizard)."""

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.settings = ctx.settings
        self.setWindowTitle(_("Welcome to MBDownloader"))
        self.setWindowIcon(qtutil.app_icon())
        self.setMinimumWidth(560)
        self.step = 0
        self.lay = QVBoxLayout(self)
        self.lay.setSpacing(12)

        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(qtutil.app_icon().pixmap(56, 56))
        head.addWidget(icon)
        col = QVBoxLayout()
        self.title = QLabel(_("Welcome to MBDownloader"))
        f = self.title.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        self.title.setFont(f)
        self.subtitle = QLabel()
        self.subtitle.setEnabled(False)
        col.addWidget(self.title)
        col.addWidget(self.subtitle)
        head.addLayout(col, 1)
        self.lay.addLayout(head)

        self.page0 = QWidget()
        p0 = QVBoxLayout(self.page0)
        p0.setContentsMargins(0, 0, 0, 0)
        p0.addWidget(hint_label(_("Browser downloads and copied links with these extensions are handed to the app. Everything else stays in the browser. You can change this later in Settings ▸ File Types.")))
        p0.addWidget(CaptureTypesEditor(self.settings))
        watch = QCheckBox(_("Watch the clipboard for links and ask to download them"))
        watch.setChecked(self.settings.watchClipboard)
        watch.toggled.connect(lambda on: setattr(self.settings, "watchClipboard", on))
        p0.addWidget(watch)

        self.page1 = QWidget()
        p1 = QVBoxLayout(self.page1)
        p1.setContentsMargins(0, 0, 0, 0)
        p1.addWidget(hint_label(_("The extensions let your browsers hand their downloads to MBDownloader and capture videos on pages. A guided setup walks you through each browser, one at a time. You can run it later from Settings ▸ Browser Extensions.")))
        found = browsers.installed_browsers()
        for k in found:
            p1.addWidget(QLabel("•  " + browsers.NAMES[k]))
        if not found:
            p1.addWidget(QLabel(_("No supported browser was found.")))
        self.lay.addWidget(self.page0)
        self.lay.addWidget(self.page1)

        row = QHBoxLayout()
        self.back = QPushButton(_("Back"))
        self.back.clicked.connect(lambda: self._go(0))
        self.later = QPushButton(_("Not Now"))
        self.later.clicked.connect(lambda: self._finish(False))
        self.next = QPushButton()
        self.next.setDefault(True)
        self.next.clicked.connect(self._next)
        row.addWidget(self.back)
        row.addStretch(1)
        row.addWidget(self.later)
        row.addWidget(self.next)
        self.lay.addLayout(row)
        self._go(0)

    def _go(self, step):
        self.step = step
        self.page0.setVisible(step == 0)
        self.page1.setVisible(step == 1)
        self.back.setVisible(step == 1)
        self.later.setVisible(step == 1)
        self.subtitle.setText(_("Which file types should be captured?") if step == 0 else _("Install the browser extensions?"))
        self.next.setText(_("Continue") if step == 0 else _("Start Setup"))
        self.adjustSize()

    def _next(self):
        if self.step == 0:
            self._go(1)
        else:
            self._finish(True)

    def _finish(self, start_setup: bool):
        self.settings.onboardingDone = True
        winshell.register_login_item_by_default_if_needed(self.settings)
        self.ctx.store.prepare_download_folder()
        self.accept()
        if start_setup:
            QTimer.singleShot(300, self.ctx.show_wizard)
