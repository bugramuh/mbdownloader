"""Main window: category sidebar, sortable download table, search, toolbar, status bar."""
from PySide6.QtCore import QAbstractTableModel, QModelIndex, QRectF, QSortFilterProxyModel, Qt, Signal
from PySide6.QtGui import QAction, QColor, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMainWindow,
                               QMenu, QSizePolicy, QSplitter, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QTableView, QToolBar, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from .. import clipboard, fmt, paths, qtutil, winshell
from ..i18n import _
from ..models import (ACTIVE_STATES, CANCELLED, CATEGORIES, COMPLETED, DOWNLOADING, FAILED, PAUSED, PROBING, QUEUED,
                      category_label, state_label)
from .widgets import paint_segment_bar

COLUMNS = ["Name", "Size", "Status", "Speed", "Time Left", "Date Added"]
ID_ROLE = Qt.UserRole + 1
SORT_ROLE = Qt.UserRole + 2


def status_text(item) -> str:
    s = item.state
    if s == COMPLETED:
        return _("Completed")
    if s == QUEUED:
        return _("Added")
    if s == FAILED:
        return _("Error: {0}", item.errorMessage or "")
    if s == CANCELLED:
        return _("Cancelled")
    pct = f"{int(item.progress * 100)}% " if item.totalBytes > 0 else ""
    if item.kind == "video" and s == DOWNLOADING and item.stage:
        return pct + item.stage
    return pct + state_label(s)


def shows_bar(item) -> bool:
    return item.state in (DOWNLOADING, PAUSED, PROBING) or (item.state == QUEUED and item.receivedBytes > 0)


class DownloadsModel(QAbstractTableModel):
    def __init__(self, store):
        super().__init__()
        self.store = store
        self._ids = [i.id for i in store.items]
        store.changed.connect(self.refresh)

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.store.items)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation == Qt.Horizontal and role == Qt.DisplayRole:
            return _(COLUMNS[section])
        return None

    def refresh(self):
        ids = [i.id for i in self.store.items]
        if ids != self._ids:
            self.beginResetModel()
            self._ids = ids
            self.endResetModel()
        elif ids:
            self.dataChanged.emit(self.index(0, 0), self.index(len(ids) - 1, len(COLUMNS) - 1))

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.store.items):
            return None
        item = self.store.items[index.row()]
        c = index.column()
        if role == ID_ROLE:
            return item.id
        if role == Qt.DecorationRole and c == 0:
            return qtutil.glyph_icon(item.category)
        if role == Qt.ToolTipRole:
            return item.url
        if role == SORT_ROLE:
            return [item.fileName.lower(), item.totalBytes, item.state, item.speed, item.eta or 0, item.createdAt][c]
        if role == Qt.DisplayRole:
            if c == 0:
                return item.fileName
            if c == 1:
                n = item.totalBytes if item.totalBytes > 0 else item.receivedBytes
                return fmt.size(n) if n > 0 else "—"
            if c == 2:
                return status_text(item)
            if c == 3:
                return fmt.speed(item.speed) if item.state == DOWNLOADING and item.speed > 0 else ""
            if c == 4:
                return fmt.duration(item.eta) if item.state == DOWNLOADING and item.eta is not None else ""
            if c == 5:
                return fmt.relative(item.createdAt)
        if role == Qt.ForegroundRole and c == 2 and item.state == FAILED:
            return qtutil.RED
        if role == Qt.TextAlignmentRole and c in (1, 3, 4):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        return None


class StatusDelegate(QStyledItemDelegate):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store

    def paint(self, painter, option, index):
        item = self.store.item(index.data(ID_ROLE))
        if item and shows_bar(item):
            self._paint_with_bar(painter, option, index, item)
        else:
            super().paint(painter, option, index)

    def _paint_with_bar(self, painter: QPainter, option, index, item):
        style = option.widget.style() if option.widget else QApplication.style()
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        text = opt.text
        opt.text = ""
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, option.widget)
        r = option.rect.adjusted(6, 3, -6, -3)
        fm = option.fontMetrics
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        painter.setPen(option.palette.highlightedText().color() if selected else option.palette.text().color())
        painter.drawText(QRectF(r.x(), r.y(), r.width(), r.height() - 9), Qt.AlignLeft | Qt.AlignVCenter,
                         fm.elidedText(text, Qt.ElideRight, r.width()))
        painter.restore()
        tint = qtutil.ORANGE if item.state == PAUSED else QColor(option.palette.highlight().color())
        if selected:
            tint = QColor(255, 255, 255)
        bar = QRectF(r.x(), r.bottom() - 5, min(r.width(), 160), 5)
        paint_segment_bar(painter, bar, item.segments, item.totalBytes, item.progress, tint)

    def sizeHint(self, option, index):
        s = super().sizeHint(option, index)
        s.setHeight(max(s.height(), 40))
        return s


class SortProxy(QSortFilterProxyModel):
    def __init__(self, store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setSortRole(SORT_ROLE)
        self.setDynamicSortFilter(True)
        self.mode = ("all", None)
        self.search = ""

    def set_mode(self, mode):
        self.mode = mode
        self.invalidateFilter()

    def set_search(self, text):
        self.search = text.lower()
        self.invalidateFilter()

    def filterAcceptsRow(self, row, parent):
        if row >= len(self.store.items):
            return False
        item = self.store.items[row]
        kind, arg = self.mode
        if kind == "category" and item.category != arg:
            return False
        if kind == "finished" and not item.is_finished:
            return False
        if kind == "unfinished" and item.is_finished:
            return False
        if self.search and self.search not in item.fileName.lower() and self.search not in item.url.lower():
            return False
        return True


class MainWindow(QMainWindow):
    show_add = Signal()

    def __init__(self, ctx):
        super().__init__()
        self.ctx = ctx
        self.store, self.settings, self.server = ctx.store, ctx.settings, ctx.server
        self.setWindowTitle(paths.APP_NAME)
        self.setWindowIcon(qtutil.app_icon())
        self.resize(1100, 640)
        self.setMinimumSize(860, 480)
        self.setAcceptDrops(True)

        self.model = DownloadsModel(self.store)
        self.proxy = SortProxy(self.store)
        self.proxy.setSourceModel(self.model)

        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setShowGrid(False)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(5, Qt.DescendingOrder)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.doubleClicked.connect(lambda ix: self.ctx.detail.show_download(ix.data(ID_ROLE)))
        self.table.setItemDelegateForColumn(2, StatusDelegate(self.store, self.table))
        self.table.setTextElideMode(Qt.ElideMiddle)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for col, w in ((1, 95), (2, 190), (3, 95), (4, 95), (5, 120)):
            h.setSectionResizeMode(col, QHeaderView.Interactive)
            self.table.setColumnWidth(col, w)
        h.setStretchLastSection(False)

        self.sidebar = QTreeWidget()
        self.sidebar.setHeaderHidden(True)
        self.sidebar.setMinimumWidth(180)
        self.sidebar.setMaximumWidth(280)
        self.sidebar.setIndentation(14)
        self._build_sidebar()
        self.sidebar.currentItemChanged.connect(self._sidebar_changed)

        self.empty = QWidget()
        el = QVBoxLayout(self.empty)
        el.setAlignment(Qt.AlignCenter)
        self.empty_icon = QLabel()
        self.empty_icon.setPixmap(qtutil.glyph_pixmap("download", 56, qtutil.secondary_color()))
        self.empty_icon.setAlignment(Qt.AlignCenter)
        self.empty_title = QLabel()
        f = self.empty_title.font()
        f.setPointSize(f.pointSize() + 4)
        self.empty_title.setFont(f)
        self.empty_title.setAlignment(Qt.AlignCenter)
        self.empty_text = QLabel(_("Download a file from your browser, drop a link here,\nor press Ctrl+N to enter a URL."))
        self.empty_text.setAlignment(Qt.AlignCenter)
        self.empty_text.setEnabled(False)
        for w in (self.empty_icon, self.empty_title, self.empty_text):
            el.addWidget(w)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        rl.addWidget(self.table, 1)
        rl.addWidget(self.empty, 1)
        rl.addWidget(self._build_status_bar())

        split = QSplitter()
        split.addWidget(self.sidebar)
        split.addWidget(right)
        split.setStretchFactor(1, 1)
        split.setSizes([210, 890])
        self.setCentralWidget(split)
        self._build_toolbar()

        QShortcut(QKeySequence.Paste, self, activated=self._paste)
        QShortcut(QKeySequence.Delete, self, activated=self._delete_selected)
        QShortcut(QKeySequence.New, self, activated=self.show_add_dialog)
        QShortcut(QKeySequence.Quit, self, activated=ctx.quit)

        self.store.changed.connect(self._update_chrome)
        self.server.status_changed.connect(self._update_chrome)
        self.proxy.rowsInserted.connect(self._update_empty)
        self.proxy.rowsRemoved.connect(self._update_empty)
        self.proxy.modelReset.connect(self._update_empty)
        self.proxy.layoutChanged.connect(self._update_empty)
        self._update_chrome()

    # --- Sidebar ---

    def _build_sidebar(self):
        self.sidebar.clear()
        self._side = {}
        root = QTreeWidgetItem([""])
        root.setIcon(0, qtutil.glyph_icon("folder"))
        root.setData(0, Qt.UserRole, ("all", None))
        f = root.font(0)
        f.setBold(True)
        root.setFont(0, f)
        self.sidebar.addTopLevelItem(root)
        self._side[("all", None)] = root
        for c in CATEGORIES:
            ch = QTreeWidgetItem(root, [""])
            ch.setIcon(0, qtutil.glyph_icon(c))
            ch.setData(0, Qt.UserRole, ("category", c))
            self._side[("category", c)] = ch
        for key, icon in (("finished", "finished"), ("unfinished", "unfinished")):
            it = QTreeWidgetItem([""])
            it.setIcon(0, qtutil.glyph_icon(icon))
            it.setData(0, Qt.UserRole, (key, None))
            it.setFont(0, f)
            self.sidebar.addTopLevelItem(it)
            self._side[(key, None)] = it
        root.setExpanded(True)
        self.sidebar.setCurrentItem(root)
        self._update_sidebar_labels()

    def _update_sidebar_labels(self):
        items = self.store.items
        counts = {("all", None): len(items), ("finished", None): sum(1 for i in items if i.is_finished),
                  ("unfinished", None): sum(1 for i in items if not i.is_finished)}
        for c in CATEGORIES:
            counts[("category", c)] = sum(1 for i in items if i.category == c)
        names = {("all", None): _("All"), ("finished", None): _("Finished"), ("unfinished", None): _("Unfinished")}
        for c in CATEGORIES:
            names[("category", c)] = category_label(c)
        for key, it in self._side.items():
            n = counts[key]
            it.setText(0, f"{names[key]}  ({n})" if n else names[key])

    def _sidebar_changed(self, cur, _prev):
        if cur:
            self.proxy.set_mode(cur.data(0, Qt.UserRole))
            self._update_empty()

    # --- Toolbar / status bar ---

    def _build_toolbar(self):
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(tb)

        def act(icon, text, tip, fn):
            a = QAction(qtutil.glyph_icon(icon), text, self)
            a.setToolTip(tip)
            a.triggered.connect(lambda _c=False: fn())
            tb.addAction(a)
            return a
        act("link", _("Add URL"), _("Add a download by URL (Ctrl+N)"), self.show_add_dialog)
        act("play", _("Start Queue"), _("Resume paused and failed downloads"), self.store.resume_all)
        act("stop", _("Stop All"), _("Pause all downloads"), self.store.pause_all)
        act("clear", _("Clear Finished"), _("Remove completed and cancelled items from the list"), self.store.clear_finished)
        act("settings", _("Settings"), _("Settings"), self.ctx.show_settings)
        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)
        self.search = QLineEdit()
        self.search.setPlaceholderText(_("Search in the list"))
        self.search.setClearButtonEnabled(True)
        self.search.setFixedWidth(240)
        self.search.textChanged.connect(self.proxy.set_search)
        self.search.textChanged.connect(lambda _t: self._update_empty())
        tb.addWidget(self.search)

    def _build_status_bar(self) -> QWidget:
        bar = QWidget()
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(12, 6, 12, 6)
        lay.setSpacing(16)
        self.dot = QLabel()
        self.dot.setFixedSize(8, 8)
        self.bridge_label = QLabel()
        self.bridge_label.setEnabled(False)
        self.unfinished_label = QLabel()
        self.unfinished_label.setEnabled(False)
        self.unfinished_label.setToolTip(_("Unfinished downloads"))
        self.speed_label = QLabel()
        self.speed_label.setEnabled(False)
        self.speed_label.setToolTip(_("Total download speed"))
        lay.addWidget(self.dot)
        lay.addWidget(self.bridge_label)
        lay.addStretch(1)
        lay.addWidget(self.unfinished_label)
        lay.addWidget(self.speed_label)
        return bar

    def _update_chrome(self):
        self._update_sidebar_labels()
        ok = self.server.running
        self.dot.setStyleSheet(f"background:{'#2ea44f' if ok else '#d1242f'}; border-radius:4px;")
        self.bridge_label.setText(_("Browser bridge is running") if ok else self.server.status_text)
        self.unfinished_label.setText(f"{qtutil.GLYPHS['list']}  {sum(1 for i in self.store.items if not i.is_finished)}")
        self.speed_label.setText(f"{qtutil.GLYPHS['speed']}  {fmt.speed(self.store.total_speed)}")
        for lb in (self.unfinished_label, self.speed_label):
            f = lb.font()
            f.setFamilies([qtutil.glyph_family() or f.family(), f.family()])
            lb.setFont(f)
        self._update_empty()

    def _update_empty(self, *_a):
        has_rows = self.proxy.rowCount() > 0
        self.table.setVisible(has_rows)
        self.empty.setVisible(not has_rows)
        self.empty_title.setText(_("No downloads in this view") if not self.search.text() else _("No matching downloads"))
        self.empty_text.setVisible(not self.search.text())

    # --- Actions ---

    def show_add_dialog(self):
        from .dialogs import AddDownloadDialog
        AddDownloadDialog(self.ctx, self).exec()

    def _selected_ids(self) -> list[str]:
        return [ix.data(ID_ROLE) for ix in self.table.selectionModel().selectedRows()]

    def _delete_selected(self):
        if self.search.hasFocus():
            return
        for id_ in self._selected_ids():
            self.store.remove(id_)

    def _paste(self):
        if self.search.hasFocus() or QApplication.focusWidget() is self.search:
            self.search.paste()
            return
        text = QApplication.clipboard().text()
        if text:
            self.ctx.clipboard.handle_paste(text)

    def _context_menu(self, pos):
        ids = self._selected_ids()
        ix = self.table.indexAt(pos)
        if ix.isValid() and ix.data(ID_ROLE) not in ids:
            self.table.selectRow(ix.row())
            ids = [ix.data(ID_ROLE)]
        menu = QMenu(self)
        items = [i for i in (self.store.item(x) for x in ids) if i]
        if not items:
            menu.addAction(_("Add URL…"), self.show_add_dialog)
        else:
            menu.addAction(_("Open Download Window"), lambda: [self.ctx.detail.show_download(i.id) for i in items])
            menu.addSeparator()
            if any(i.state == QUEUED for i in items):
                menu.addAction(_("Start Download"), lambda: [self.store.start(i.id) for i in items])
            if any(i.state in ACTIVE_STATES or i.state == QUEUED for i in items):
                menu.addAction(_("Pause"), lambda: [self.store.pause(i.id) for i in items])
                menu.addAction(_("Cancel"), lambda: [self.store.cancel(i.id) for i in items])
            if any(i.state in (PAUSED, FAILED, CANCELLED) for i in items):
                menu.addAction(_("Resume / Retry"), lambda: [self.store.resume(i.id) for i in items])
            failed = [i for i in items if i.kind == "file" and i.state == FAILED]
            if failed:
                menu.addAction(_("Download in Browser"), lambda: [self.store.hand_off_to_browser(i.id) for i in failed])
            if len(items) == 1:
                it = items[0]
                menu.addSeparator()
                menu.addAction(_("Copy URL"), lambda: QApplication.clipboard().setText(it.url))
                if it.state == COMPLETED:
                    menu.addAction(_("Open"), lambda: self.store.open(it))
                    menu.addAction(_("Show in Explorer"), lambda: self.store.reveal(it))
            menu.addSeparator()
            menu.addAction(_("Remove from List"), lambda: [self.store.remove(i.id) for i in items])
            if any(i.state == COMPLETED for i in items):
                menu.addAction(_("Move File to Recycle Bin and Remove"), lambda: [self.store.remove(i.id, delete_file=True) for i in items])
        menu.exec(self.table.viewport().mapToGlobal(pos))

    # --- Drag & drop ---

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls() or e.mimeData().hasText():
            e.acceptProposedAction()

    def dropEvent(self, e):
        md = e.mimeData()
        if md.hasUrls():
            text = "\n".join(u.toString() for u in md.urls() if u.scheme() in ("http", "https"))
        else:
            text = "\n".join(clipboard.extract_urls(md.text()))
        if text:
            self.store.add_urls(text, "file")
            e.acceptProposedAction()

    def closeEvent(self, e):
        # Closing the window keeps the app running in the tray; quit from the tray menu.
        e.ignore()
        self.hide()
