"""The download window: one download's details, plus a list on the left while it tracks several downloads."""
import os

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QCheckBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPlainTextEdit, QProgressBar, QPushButton, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget, QHeaderView, QAbstractItemView)

from .. import fmt, paths, qtutil
from ..i18n import _
from ..models import (CANCELLED, COMPLETED, DOWNLOADING, FAILED, PAUSED, PROBING, QUEUED, state_label)
from .widgets import SegmentBar

FULL_H, COMPACT_H, COMPLETED_H, DETAIL_W, LIST_W = 540, 290, 250, 680, 230


def elide(label: QLabel, text: str):
    label.setText(text)
    label.setToolTip(text)
    label.setTextInteractionFlags(Qt.TextSelectableByMouse)


class DetailView(QWidget):
    """Details of one download; switches to a 'completed' layout when it finishes."""

    def __init__(self, ctx, id_: str, manager):
        super().__init__()
        self.ctx, self.id, self.manager = ctx, id_, manager
        self.store, self.settings = ctx.store, ctx.settings
        self.show_details = True
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 16, 16, 16)
        self.stack = QStackedWidget()
        root.addWidget(self.stack)
        self.stack.addWidget(self._build_active())
        self.stack.addWidget(self._build_completed())
        self.stack.addWidget(QLabel(_("This download was removed from the list.")))

    # --- Active ---

    def _build_active(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.fields = {}
        for r, (key, label) in enumerate([("url", _("URL")), ("status", _("Status")), ("size", _("File size")),
                                           ("got", _("Downloaded")), ("rate", _("Transfer rate")), ("eta", _("Time left")),
                                           ("resume", _("Resume capability")), ("save", _("Save to"))]):
            lb = QLabel(label)
            lb.setEnabled(False)
            val = QLabel()
            val.setMinimumWidth(10)
            grid.addWidget(lb, r, 0, Qt.AlignTop)
            grid.addWidget(val, r, 1)
            self.fields[key] = val
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(10)
        lay.addWidget(self.bar)

        row = QHBoxLayout()
        self.toggle_btn = QPushButton()
        self.toggle_btn.clicked.connect(self._toggle_details)
        self.autoclose = QCheckBox(_("Close when completed"))
        self.autoclose.setChecked(self.settings.autoCloseDetail)
        self.autoclose.toggled.connect(lambda on: setattr(self.settings, "autoCloseDetail", on))
        row.addWidget(self.toggle_btn)
        row.addWidget(self.autoclose)
        row.addStretch(1)
        self.btn_start = QPushButton(_("Start Download"))
        self.btn_pause = QPushButton(_("Pause"))
        self.btn_cancel = QPushButton(_("Cancel"))
        self.btn_browser = QPushButton(_("Download in Browser"))
        self.btn_resume = QPushButton(_("Resume"))
        self.btn_remove = QPushButton(_("Remove from List"))
        self.btn_start.clicked.connect(lambda: self.store.start(self.id))
        self.btn_pause.clicked.connect(lambda: self.store.pause(self.id))
        self.btn_cancel.clicked.connect(lambda: self.store.cancel(self.id))
        self.btn_browser.clicked.connect(lambda: (self.store.hand_off_to_browser(self.id), self.manager.close_id(self.id)))
        self.btn_resume.clicked.connect(lambda: self.store.resume(self.id))
        self.btn_remove.clicked.connect(lambda: (self.store.remove(self.id), self.manager.close_id(self.id)))
        for b in (self.btn_start, self.btn_pause, self.btn_cancel, self.btn_browser, self.btn_resume, self.btn_remove):
            row.addWidget(b)
        lay.addLayout(row)

        self.sep = QFrame()
        self.sep.setFrameShape(QFrame.HLine)
        self.sep.setFrameShadow(QFrame.Sunken)
        lay.addWidget(self.sep)

        # Connections (files)
        self.conn_box = QWidget()
        cl = QVBoxLayout(self.conn_box)
        cl.setContentsMargins(0, 0, 0, 0)
        cap = QLabel(_("Start positions and download progress by connections"))
        cap.setAlignment(Qt.AlignCenter)
        cap.setEnabled(False)
        cl.addWidget(cap)
        self.seg_bar = SegmentBar(12)
        cl.addWidget(self.seg_bar)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([_("N."), _("Downloaded"), _("Start"), _("Info")])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 40)
        self.table.setColumnWidth(1, 110)
        self.table.setColumnWidth(2, 110)
        self.table.setMinimumHeight(160)
        cl.addWidget(self.table, 1)
        lay.addWidget(self.conn_box, 1)

        # Log (videos)
        self.log_box = QWidget()
        ll = QVBoxLayout(self.log_box)
        ll.setContentsMargins(0, 0, 0, 0)
        cap2 = QLabel(_("yt-dlp output"))
        cap2.setEnabled(False)
        ll.addWidget(cap2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.log.setFont(mono)
        self.log.setMinimumHeight(160)
        ll.addWidget(self.log, 1)
        lay.addWidget(self.log_box, 1)
        self._update_toggle_text()
        return w

    def _update_toggle_text(self):
        self.toggle_btn.setText(_("<< Hide Details") if self.show_details else _("Show Details >>"))

    def _toggle_details(self):
        self.show_details = not self.show_details
        self._update_toggle_text()
        self.manager.set_compact(not self.show_details)

    # --- Completed ---

    def _build_completed(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(14)
        head = QHBoxLayout()
        ic = QLabel()
        ic.setPixmap(qtutil.glyph_pixmap("checkfill", 44, qtutil.GREEN))
        head.addWidget(ic)
        col = QVBoxLayout()
        t = QLabel(_("Download completed"))
        f = t.font()
        f.setPointSize(f.pointSize() + 4)
        f.setBold(True)
        t.setFont(f)
        self.done_name = QLabel()
        col.addWidget(t)
        col.addWidget(self.done_name)
        head.addLayout(col, 1)
        lay.addLayout(head)
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        self.done_fields = {}
        for r, (k, label) in enumerate([("size", _("Size")), ("loc", _("Location")), ("url", _("URL")), ("when", _("Finished"))]):
            lb = QLabel(label)
            lb.setEnabled(False)
            v = QLabel()
            grid.addWidget(lb, r, 0)
            grid.addWidget(v, r, 1)
            self.done_fields[k] = v
        grid.setColumnStretch(1, 1)
        lay.addLayout(grid)
        lay.addStretch(1)
        row = QHBoxLayout()
        close = QPushButton(_("Close"))
        close.clicked.connect(lambda: self.manager.close_id(self.id))
        show = QPushButton(_("Show in Explorer"))
        show.clicked.connect(self._show_in_explorer)
        opn = QPushButton(_("Open"))
        opn.setDefault(True)
        opn.clicked.connect(self._open)
        row.addWidget(close)
        row.addStretch(1)
        row.addWidget(show)
        row.addWidget(opn)
        lay.addLayout(row)
        return w

    def _show_in_explorer(self):
        item = self.store.item(self.id)
        if item:
            self.store.reveal(item)
            self.manager.close_if_alone(self.id)

    def _open(self):
        item = self.store.item(self.id)
        if item:
            self.store.open(item)
            self.manager.close_id(self.id)

    # --- Refresh ---

    def refresh(self):
        item = self.store.item(self.id)
        if not item:
            self.stack.setCurrentIndex(2)
            return
        if item.state == COMPLETED:
            self.stack.setCurrentIndex(1)
            self.done_name.setText(item.fileName)
            elide(self.done_name, item.fileName)
            self.done_fields["size"].setText(fmt.size(item.totalBytes))
            elide(self.done_fields["loc"], os.path.dirname(item.finalPath) if item.finalPath else self.store.destination_dir(item))
            elide(self.done_fields["url"], item.url)
            self.done_fields["when"].setText(fmt.date_time(item.completedAt) if item.completedAt else "")
            return
        self.stack.setCurrentIndex(0)
        f = self.fields
        elide(f["url"], item.url)
        f["status"].setText(self._status_text(item))
        f["status"].setStyleSheet("color:#d1242f" if item.state == FAILED else "")
        f["size"].setText(fmt.size(item.totalBytes) if item.totalBytes > 0 else _("Unknown"))
        f["got"].setText(f"{fmt.size(item.receivedBytes)}  ({int(item.progress * 100)}%)" if item.totalBytes > 0 else fmt.size(item.receivedBytes))
        f["rate"].setText(fmt.speed(item.speed) if item.state == DOWNLOADING else "—")
        f["eta"].setText(fmt.duration(item.eta) if item.eta is not None else "—")
        if item.kind == "video":
            resume = "yt-dlp"
        elif item.supportsRanges:
            resume = _("Yes")
        else:
            resume = "?" if item.state in (QUEUED, PROBING) else _("No")
        f["resume"].setText(resume)
        elide(f["save"], item.finalPath or self.store.destination_path(item))
        self.bar.setValue(int(item.progress * 1000) if item.totalBytes > 0 else 0)

        s = item.state
        self.btn_start.setVisible(s == QUEUED)
        self.btn_pause.setVisible(s in (DOWNLOADING, PROBING))
        self.btn_cancel.setVisible(s in (QUEUED, DOWNLOADING, PROBING))
        self.btn_browser.setVisible(s == FAILED and item.kind == "file")
        self.btn_resume.setVisible(s in (PAUSED, FAILED, CANCELLED))
        self.btn_remove.setVisible(s in (PAUSED, FAILED, CANCELLED))
        self.btn_resume.setDefault(s in (PAUSED, FAILED, CANCELLED))
        self.btn_start.setDefault(s == QUEUED)
        if self.autoclose.isChecked() != self.settings.autoCloseDetail:
            self.autoclose.setChecked(self.settings.autoCloseDetail)

        self.sep.setVisible(self.show_details)
        video = item.kind == "video"
        self.conn_box.setVisible(self.show_details and not video)
        self.log_box.setVisible(self.show_details and video)
        if self.show_details:
            if video:
                text = item.log or _("Waiting…")
                if self.log.toPlainText() != text:
                    bar = self.log.verticalScrollBar()
                    at_end = bar.value() >= bar.maximum() - 4
                    self.log.setPlainText(text)
                    if at_end:
                        bar.setValue(bar.maximum())
            else:
                self._fill_connections(item)

    def _status_text(self, item) -> str:
        if item.state == DOWNLOADING:
            if item.kind == "video":
                return item.stage or _("Downloading with yt-dlp…")
            return _("Receiving data…")
        if item.state == FAILED:
            return item.errorMessage or _("Error")
        return state_label(item.state)

    def _fill_connections(self, item):
        tint = qtutil.secondary_color() if item.state == PAUSED else None
        self.seg_bar.set_state(item.segments, item.totalBytes, item.progress, tint)
        rows = []
        if not item.segments:
            if item.state != QUEUED:
                info = {PROBING: _("Sending GET…"), DOWNLOADING: _("Receiving data… (single stream)"), PAUSED: _("Paused")}.get(item.state, state_label(item.state))
                rows.append((1, item.receivedBytes, 0, info))
        else:
            for i, s in enumerate(item.segments):
                if s.done:
                    info = _("Completed")
                elif item.state == DOWNLOADING:
                    info = _("Sending GET…") if s.received == 0 else _("Receiving data…")
                elif item.state == PAUSED:
                    info = _("Paused")
                else:
                    info = state_label(item.state)
                rows.append((i + 1, s.received, s.start, info))
        if self.table.rowCount() != len(rows):
            self.table.setRowCount(len(rows))
        for r, (n, got, start, info) in enumerate(rows):
            for c, text in enumerate([str(n), fmt.size(got), fmt.size(start), info]):
                it = self.table.item(r, c)
                if it is None:
                    it = QTableWidgetItem()
                    self.table.setItem(r, c, it)
                if it.text() != text:
                    it.setText(text)


class PanelRow(QWidget):
    def __init__(self):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 4, 6, 4)
        self.icon = QLabel()
        self.icon.setFixedWidth(20)
        lay.addWidget(self.icon, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.name = QLabel()
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(5)
        self.status = QLabel()
        self.status.setEnabled(False)
        col.addWidget(self.name)
        col.addWidget(self.bar)
        col.addWidget(self.status)
        lay.addLayout(col, 1)

    def update_from(self, item):
        if item.state == COMPLETED:
            self.icon.setPixmap(qtutil.glyph_pixmap("checkfill", 16, qtutil.GREEN))
        else:
            self.icon.setPixmap(qtutil.glyph_pixmap(item.category, 16, qtutil.RED if item.state == FAILED else None))
        self.name.setText(self.name.fontMetrics().elidedText(item.fileName, Qt.ElideMiddle, 160))
        self.bar.setVisible(item.is_active or item.state == PAUSED)
        self.bar.setValue(int(item.progress * 1000) if item.totalBytes > 0 else 0)
        if item.state == DOWNLOADING:
            pct = f"{int(item.progress * 100)}%" if item.totalBytes > 0 else fmt.size(item.receivedBytes)
            text = f"{pct} · {fmt.speed(item.speed)}" if item.speed > 0 else pct
        elif item.state == COMPLETED:
            text = fmt.size(item.totalBytes)
        else:
            text = state_label(item.state)
        self.status.setText(self.status.fontMetrics().elidedText(text, Qt.ElideRight, 170))


class DetailWindow(QWidget):
    """Shared download window, opened from anywhere (for example on a request from the browser)."""

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.setWindowIcon(qtutil.app_icon())
        self.tracked: list[str] = []
        self.selected: str | None = None
        self.compact = False
        self.views: dict[str, DetailView] = {}
        self.rows: dict[str, tuple[QListWidgetItem, PanelRow]] = {}
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.list = QListWidget()
        self.list.setFixedWidth(LIST_W)
        self.list.currentItemChanged.connect(self._list_changed)
        lay.addWidget(self.list)
        self.divider = QFrame()
        self.divider.setFrameShape(QFrame.VLine)
        self.divider.setFrameShadow(QFrame.Sunken)
        lay.addWidget(self.divider)
        self.stack = QStackedWidget()
        lay.addWidget(self.stack, 1)
        self.stack.setMinimumWidth(DETAIL_W)
        ctx.store.changed.connect(self.refresh)
        ctx.store.item_completed.connect(self._completed)

    @property
    def shows_list(self) -> bool:
        return len(self.tracked) > 1

    def show_download(self, id_: str):
        had_list = self.shows_list
        if id_ not in self.tracked:
            self.tracked.append(id_)
            self._add_widgets(id_)
        self.selected = id_
        self._select(id_)
        if self.shows_list != had_list or not self.isVisible():
            self._layout()
        self.refresh()
        qtutil.bring_to_front(self)
        QTimer.singleShot(300, lambda: self.isVisible() and qtutil.bring_to_front(self))

    def _add_widgets(self, id_: str):
        view = DetailView(self.ctx, id_, self)
        self.views[id_] = view
        self.stack.addWidget(view)
        li = QListWidgetItem()
        li.setData(Qt.UserRole, id_)
        row = PanelRow()
        li.setSizeHint(row.sizeHint().expandedTo(row.minimumSizeHint()))
        self.list.addItem(li)
        self.list.setItemWidget(li, row)
        self.rows[id_] = (li, row)

    def _remove_widgets(self, id_: str):
        view = self.views.pop(id_, None)
        if view:
            self.stack.removeWidget(view)
            view.deleteLater()
        li_row = self.rows.pop(id_, None)
        if li_row:
            self.list.takeItem(self.list.row(li_row[0]))

    def _select(self, id_: str):
        view = self.views.get(id_)
        if view:
            self.stack.setCurrentWidget(view)
        li_row = self.rows.get(id_)
        if li_row:
            self.list.blockSignals(True)
            self.list.setCurrentItem(li_row[0])
            self.list.blockSignals(False)

    def _list_changed(self, cur, _prev):
        if cur:
            self.selected = cur.data(Qt.UserRole)
            self._select(self.selected)
            self._layout()

    def close_id(self, id_: str):
        """Takes a download out of the window; closes the window when it was the last one."""
        if id_ not in self.tracked:
            return
        if len(self.tracked) <= 1:
            self.tracked.clear()
            self._remove_widgets(id_)
            self.hide()
            return
        i = self.tracked.index(id_)
        self.tracked.remove(id_)
        self._remove_widgets(id_)
        if self.selected == id_:
            self.selected = self.tracked[min(i, len(self.tracked) - 1)]
            self._select(self.selected)
        self._layout()

    def close_if_alone(self, id_: str):
        if self.tracked == [id_]:
            self.close_id(id_)

    def set_compact(self, compact: bool):
        self.compact = compact
        self._layout()

    def _completed(self, id_: str, auto_close: bool):
        if id_ not in self.tracked:
            return
        if auto_close:
            self.close_id(id_)
        else:
            self._layout()

    def _layout(self):
        """Size for what is shown: the list keeps a full-height window; a single download sizes to its view."""
        sl = self.shows_list
        self.list.setVisible(sl)
        self.divider.setVisible(sl)
        width = DETAIL_W + (LIST_W if sl else 0)
        height = FULL_H
        item = self.ctx.store.item(self.selected) if self.selected else None
        if not sl and item:
            height = COMPLETED_H if item.state == COMPLETED else (COMPACT_H if self.compact else FULL_H)
        self.setMinimumSize(width, min(height, COMPLETED_H))
        self.resize(width if self.width() < width or not self.isVisible() else self.width(), height)

    def refresh(self):
        if not self.tracked:
            return
        store = self.ctx.store
        # Downloads removed from the main list leave the window too.
        for id_ in [i for i in self.tracked if not store.item(i)]:
            self.close_id(id_)
        if not self.tracked:
            return
        for id_, (li, row) in self.rows.items():
            it = store.item(id_)
            if it:
                row.update_from(it)
        if self.selected in self.views:
            self.views[self.selected].refresh()
        shown = [store.item(i) for i in self.tracked if store.item(i)]
        if len(shown) == 1:
            it = shown[0]
            pct = f"{int(it.progress * 100)}% · " if it.totalBytes > 0 and it.state != COMPLETED else ""
            title = pct + it.fileName
        else:
            m = [i for i in shown if i.is_active and i.totalBytes > 0]
            total = sum(i.totalBytes for i in m)
            got = sum(min(i.receivedBytes, i.totalBytes) for i in m)
            title = (f"{int(got * 100 / total)}% · " if total > 0 else "") + _("{0} downloads", len(shown))
        if self.windowTitle() != title:
            self.setWindowTitle(title)

    def closeEvent(self, e):
        self.tracked.clear()
        for id_ in list(self.views):
            self._remove_widgets(id_)
        self.selected = None
        super().closeEvent(e)
