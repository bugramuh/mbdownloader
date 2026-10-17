"""Small shared widgets."""
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QWidget

from .. import qtutil
from ..i18n import _


def paint_segment_bar(p: QPainter, rect: QRectF, segments, total: int, fallback: float, tint: QColor):
    """Bar showing how much of each segment has been received."""
    p.save()
    p.setRenderHint(QPainter.Antialiasing)
    r = rect.height() / 2
    clip = QPainterPath()
    clip.addRoundedRect(rect, r, r)
    p.fillPath(clip, QColor(128, 128, 128, 64))
    p.setClipPath(clip)
    if not segments or total <= 0:
        p.fillRect(QRectF(rect.x(), rect.y(), rect.width() * fallback, rect.height()), tint)
    else:
        for s in segments:
            if s.received > 0:
                x = rect.x() + s.start / total * rect.width()
                w = s.received / total * rect.width()
                p.fillRect(QRectF(x, rect.y(), max(w, 1), rect.height()), tint)
        for s in segments[1:]:
            x = rect.x() + s.start / total * rect.width()
            p.fillRect(QRectF(x, rect.y(), 1, rect.height()), QColor(255, 255, 255, 150))
    p.restore()


class SegmentBar(QWidget):
    def __init__(self, height: int = 12, parent=None):
        super().__init__(parent)
        self.setFixedHeight(height)
        self._segments, self._total, self._fallback, self._tint = [], -1, 0.0, QApplication.palette().highlight().color()

    def set_state(self, segments, total, fallback, tint=None):
        self._segments, self._total, self._fallback = segments, total, fallback
        if tint is not None:
            self._tint = tint
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        paint_segment_bar(p, QRectF(0, 0, self.width(), self.height()), self._segments, self._total, self._fallback, self._tint)


class PathBox(QLineEdit):
    """Read-only, selectable monospace text (a path to copy)."""

    def __init__(self, text: str = "", parent=None):
        super().__init__(text, parent)
        self.setReadOnly(True)
        f = self.font()
        f.setFamily("Consolas")
        self.setFont(f)
        self.setCursorPosition(0)


class StatusRow(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self._icon = QLabel()
        self._text = QLabel()
        lay.addWidget(self._icon)
        lay.addWidget(self._text, 1)

    def set_state(self, ok: bool, ok_text: str, pending_text: str):
        self._icon.setPixmap(qtutil.glyph_pixmap("checkfill" if ok else "dashed", 18, qtutil.GREEN if ok else qtutil.secondary_color()))
        self._text.setText(ok_text if ok else pending_text)
        self._text.setEnabled(ok)


def hint_label(text: str) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(True)
    lb.setEnabled(False)
    return lb
