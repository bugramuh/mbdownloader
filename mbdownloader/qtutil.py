"""Qt helpers: run callables on the UI thread, app icon, glyph icons and alert boxes."""
import os

from PySide6.QtCore import QObject, QPoint, QRect, QSize, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QFontDatabase, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QCheckBox, QMessageBox, QWidget

from . import paths


class _Invoker(QObject):
    call = Signal(object)

    def __init__(self):
        super().__init__()
        self.call.connect(self._run, Qt.QueuedConnection)

    @Slot(object)
    def _run(self, fn):
        fn()


_invoker: _Invoker | None = None


def init_invoker():
    global _invoker
    _invoker = _Invoker()


def on_main(fn):
    """Queues fn() to run on the UI thread (safe from any thread)."""
    _invoker.call.emit(fn)


# --- Icons ---

_app_icon: QIcon | None = None


def app_icon() -> QIcon:
    global _app_icon
    if _app_icon is None:
        icon = QIcon()
        d = paths.resource_path("icons")
        for n in (16, 32, 128, 256, 512):
            p = os.path.join(d, f"icon_{n}x{n}.png")
            if os.path.exists(p):
                icon.addFile(p, QSize(n, n))
        _app_icon = icon
    return _app_icon


_glyph_family: str | None = None

# Segoe Fluent Icons (Windows 11) / Segoe MDL2 Assets (Windows 10) code points.
GLYPHS = {
    "image": "", "music": "", "video": "", "apps": "", "document": "",
    "compressed": "", "other": "", "folder": "", "finished": "", "unfinished": "",
    "add": "", "link": "", "play": "", "stop": "", "pause": "", "clear": "",
    "settings": "", "download": "", "check": "", "checkfill": "", "error": "",
    "globe": "", "puzzle": "", "copy": "", "refresh": "", "wand": "",
    "remove": "", "open": "", "list": "", "speed": "", "dashed": "", "circle": "",
}


def glyph_family() -> str:
    global _glyph_family
    if _glyph_family is None:
        fams = set(QFontDatabase.families())
        _glyph_family = next((f for f in ("Segoe Fluent Icons", "Segoe MDL2 Assets") if f in fams), "")
    return _glyph_family


def glyph_pixmap(name: str, size: int = 32, color: QColor | None = None) -> QPixmap:
    ratio = QGuiApplication.primaryScreen().devicePixelRatio() if QGuiApplication.primaryScreen() else 1.0
    pm = QPixmap(int(size * ratio), int(size * ratio))
    pm.setDevicePixelRatio(ratio)
    pm.fill(Qt.transparent)
    if not glyph_family():
        return pm
    p = QPainter(pm)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont(glyph_family())
    f.setPixelSize(int(size * 0.8))
    p.setFont(f)
    p.setPen(color or QApplication.palette().windowText().color())
    p.drawText(QRect(0, 0, size, size), Qt.AlignCenter, GLYPHS.get(name, name))
    p.end()
    return pm


def glyph_icon(name: str, color: QColor | None = None) -> QIcon:
    icon = QIcon()
    for s in (16, 20, 24, 32):
        icon.addPixmap(glyph_pixmap(name, s, color))
    return icon


def secondary_color() -> QColor:
    c = QApplication.palette().windowText().color()
    c.setAlphaF(0.6)
    return c


GREEN = QColor("#2ea44f")
RED = QColor("#d1242f")
ORANGE = QColor("#d9822b")


# --- Alerts ---

def ask(parent: QWidget | None, title: str, text: str, info: str, buttons: list[str], checkbox: str | None = None,
        icon=QMessageBox.Information, escape_index: int | None = None) -> tuple[int, bool]:
    """Modal alert like NSAlert: returns (index of the clicked button or -1, checkbox state)."""
    box = QMessageBox(parent)
    box.setWindowTitle(title)
    box.setWindowIcon(app_icon())
    box.setIcon(icon)
    box.setText(text)
    if info:
        box.setInformativeText(info)
    btns = []
    for i, b in enumerate(buttons):
        role = QMessageBox.AcceptRole if i == 0 else (QMessageBox.RejectRole if i == escape_index else QMessageBox.ActionRole)
        btns.append(box.addButton(b, role))
    if btns:
        box.setDefaultButton(btns[0])
    if escape_index is not None and escape_index < len(btns):
        box.setEscapeButton(btns[escape_index])
    cb = None
    if checkbox:
        cb = QCheckBox(checkbox)
        box.setCheckBox(cb)
    box.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    box.exec()
    clicked = box.clickedButton()
    idx = btns.index(clicked) if clicked in btns else -1
    return idx, bool(cb and cb.isChecked())


def bring_to_front(w: QWidget):
    w.show()
    if w.isMinimized():
        w.showNormal()
    w.raise_()
    w.activateWindow()
    try:
        import ctypes
        hwnd = int(w.winId())
        ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def center_on_screen(w: QWidget):
    screen = QGuiApplication.screenAt(QPoint(*map(int, (w.x(), w.y())))) or QGuiApplication.primaryScreen()
    if screen:
        g = screen.availableGeometry()
        w.move(g.center() - w.rect().center())
