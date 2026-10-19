"""页面共用的组合控件：布局小工具、数据表、文本查看器、指标卡、浮动提示、对话框。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import tbui as ui
from tbui._qt import QtCore, QtGui, QtWidgets, Qt, Signal

W = QtWidgets


# ── 布局与文字 ────────────────────────────────────────

def hbox(*ws, spacing=8, margins=(0, 0, 0, 0)) -> QtWidgets.QWidget:
    """None 为弹性空白，int 为固定间距。"""
    w = W.QWidget()
    lay = W.QHBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in ws:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    return w


def vbox(*ws, spacing=8, margins=(0, 0, 0, 0)) -> QtWidgets.QWidget:
    w = W.QWidget()
    lay = W.QVBoxLayout(w)
    lay.setContentsMargins(*margins)
    lay.setSpacing(spacing)
    for x in ws:
        if x is None:
            lay.addStretch(1)
        elif isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    return w


def label(text: str = "", role: str | None = None, mono: bool = False, px: int | None = None,
          wrap: bool = False, select: bool = False) -> QtWidgets.QLabel:
    l = W.QLabel(text)
    if mono:
        l.setFont(ui.theme().mono_font(px or 12))
    elif px:
        l.setFont(ui.theme().ui_font(px))
    if role:
        ui.repolish(l, role=role)
    l.setWordWrap(wrap)
    if select:
        l.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return l


def set_icon(w, name: str, color: str = "ink-muted", size: int = 16, **kw):
    """与 tbui.bind_icon 相同，但同一控件反复切换图标时只保留一个主题监听（播放 / 暂停这类按钮用）。"""
    w._icon_spec = (name, color, size, kw)
    if not getattr(w, "_icon_refresh", None):
        def refresh(*_):
            try:
                n, c, s, k = w._icon_spec
                w.setIcon(ui.theme().icon(n, c, s, **k))
                w.setIconSize(QtCore.QSize(s, s))
            except RuntimeError:  # 控件已销毁
                pass
        w._icon_refresh = refresh
        ui.theme().changed.connect(refresh)
    w._icon_refresh()


def hline() -> QtWidgets.QFrame:
    f = W.QFrame()
    ui.repolish(f, hline=True)
    return f


def vline() -> QtWidgets.QFrame:
    f = W.QFrame()
    ui.repolish(f, vline=True)
    return f


def fmt_size(n: int | None) -> str:
    if n is None:
        return "—"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def fmt_duration(s: float | None) -> str:
    if s is None:
        return "—"
    s = int(s)
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d} 天 {h} 小时"
    if h:
        return f"{h} 小时 {m} 分"
    return f"{m}:{s:02d}"


def fmt_ago(ts: float | None) -> str:
    if ts is None:
        return "—"
    d = time.time() - ts
    if d < 60:
        return "刚刚"
    if d < 3600:
        return f"{int(d // 60)} 分钟前"
    if d < 86400:
        return f"{int(d // 3600)} 小时前"
    return time.strftime("%m-%d %H:%M", time.localtime(ts))


class PanelHead(W.QFrame):
    """面板标题条：左标题 + 计数，右侧放操作。"""

    def __init__(self, title: str, *right, parent=None):
        super().__init__(parent)
        ui.repolish(self, panelhead=True)
        self.setFixedHeight(40)
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 8, 0)
        lay.setSpacing(4)
        self.title = label(title, "subtitle")
        self.count = label("", "faint", mono=True, px=11)
        lay.addWidget(self.title)
        lay.addSpacing(4)
        lay.addWidget(self.count)
        lay.addStretch(1)
        for w in right:
            lay.addWidget(w)

    def set_count(self, n: int | str | None):
        self.count.setText("" if n is None else str(n))


class BusyBar(W.QProgressBar):
    """贴在工具栏下方的不确定进度条，空闲时占位但透明，避免布局跳动。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setTextVisible(False)
        self.setFixedHeight(2)
        self.setRange(0, 1)
        self.setValue(0)
        self.setStyleSheet("QProgressBar { min-height: 2px; max-height: 2px; border-radius: 0; background: transparent; }"
                           "QProgressBar::chunk { border-radius: 0; }")
        self._n = 0

    def push(self):
        self._n += 1
        self.setRange(0, 0)

    def pop(self):
        self._n = max(0, self._n - 1)
        if not self._n:
            self.setRange(0, 1)
            self.setValue(0)


# ── 数据表 ────────────────────────────────────────────

@dataclass
class Column:
    title: str
    value: Callable[[Any], Any]                       # 显示值
    width: int | None = None                          # None：自动拉伸（最后一列）
    mono: bool = False
    align: str = "left"                               # left / right / center
    tone: Callable[[Any], str | None] | None = None   # 返回颜色令牌名
    sort: Callable[[Any], Any] | None = None          # 排序键，默认用显示值
    tip: Callable[[Any], str] | None = None
    icon: Callable[[Any], str | None] | None = None   # 返回图标名（tbui/icons）


class TableModel(QtCore.QAbstractTableModel):
    ROW = Qt.ItemDataRole.UserRole + 10

    def __init__(self, columns: list[Column], parent=None):
        super().__init__(parent)
        self.columns = columns
        self.rows: list[Any] = []
        t = ui.theme()
        self._mono = t.mono_font(12)

    def set_rows(self, rows: list):
        self.beginResetModel()
        self.rows = list(rows)
        self.endResetModel()

    def update_row(self, i: int, row):
        self.rows[i] = row
        self.dataChanged.emit(self.index(i, 0), self.index(i, len(self.columns) - 1))

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation != Qt.Orientation.Horizontal:
            return None
        c = self.columns[section]
        if role == Qt.ItemDataRole.DisplayRole:
            return c.title
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(self._align(c))
        return None

    @staticmethod
    def _align(c: Column):
        h = {"left": Qt.AlignmentFlag.AlignLeft, "right": Qt.AlignmentFlag.AlignRight, "center": Qt.AlignmentFlag.AlignHCenter}[c.align]
        return h | Qt.AlignmentFlag.AlignVCenter

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self.rows[index.row()]
        c = self.columns[index.column()]
        if role == Qt.ItemDataRole.DisplayRole:
            v = c.value(row)
            return "—" if v is None else str(v)
        if role == Qt.ItemDataRole.ToolTipRole:
            return c.tip(row) if c.tip else None
        if role == Qt.ItemDataRole.FontRole and c.mono:
            return self._mono
        if role == Qt.ItemDataRole.DecorationRole and c.icon:
            name = c.icon(row)
            return ui.theme().icon(name, "ink-muted") if name else None
        if role == Qt.ItemDataRole.ForegroundRole and c.tone:
            tok = c.tone(row)
            return ui.theme().color(tok) if tok else None
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return int(self._align(c))
        if role == Qt.ItemDataRole.UserRole:
            return (c.sort or c.value)(row)
        if role == self.ROW:
            return row
        return None


class TableFilter(QtCore.QSortFilterProxyModel):
    """文字过滤（任一列包含，不区分大小写）+ 自定义谓词；按 UserRole 排序。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.text = ""
        self.predicate: Callable[[Any], bool] | None = None
        self.setSortRole(Qt.ItemDataRole.UserRole)

    def _refilter(self, change: Callable[[], None]):
        """Qt 6.10+ 用 begin/endFilterChange，旧版本退回 invalidateFilter。"""
        if hasattr(self, "beginFilterChange"):
            self.beginFilterChange()
            change()
            self.endFilterChange(QtCore.QSortFilterProxyModel.Direction.Rows)
        else:
            change()
            self.invalidateFilter()

    def set_text(self, text: str):
        self._refilter(lambda: setattr(self, "text", text.lower()))

    def set_predicate(self, fn):
        self._refilter(lambda: setattr(self, "predicate", fn))

    def filterAcceptsRow(self, r, parent):
        m: TableModel = self.sourceModel()
        row = m.rows[r]
        if self.predicate and not self.predicate(row):
            return False
        if not self.text:
            return True
        return any(self.text in str(c.value(row) or "").lower() for c in m.columns)

    def lessThan(self, a, b):
        x, y = a.data(Qt.ItemDataRole.UserRole), b.data(Qt.ItemDataRole.UserRole)
        if x is None:
            return y is not None
        if y is None:
            return False
        try:
            return x < y
        except TypeError:
            return str(x) < str(y)


class DataTable(W.QTableView):
    """绑定 TableModel + TableFilter 的表格。current_row() / selected_rows() 返回原始对象。"""

    rowActivated = Signal(object)        # 双击 / 回车
    selectionChangedRows = Signal(list)

    def __init__(self, columns: list[Column], sortable: bool = True, multi: bool = False, parent=None):
        super().__init__(parent)
        self.table_model = TableModel(columns, self)
        self.filter = TableFilter(self)
        self.filter.setSourceModel(self.table_model)
        self.setModel(self.filter)
        ui.repolish(self, data=True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setAlternatingRowColors(False)
        self.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection if multi else W.QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollMode(W.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setVerticalScrollMode(W.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        vh = self.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(W.QHeaderView.ResizeMode.Fixed)
        vh.setDefaultSectionSize(28)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setFixedHeight(28)
        hh.setFont(ui.theme().label_font())
        hh.setStretchLastSection(True)
        for i, c in enumerate(columns):
            if c.width:
                hh.resizeSection(i, c.width)
        self.setSortingEnabled(sortable)
        if sortable:
            hh.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        self.doubleClicked.connect(lambda idx: self.rowActivated.emit(self._row(idx)))
        self.selectionModel().selectionChanged.connect(lambda *_: self.selectionChangedRows.emit(self.selected_rows()))
        ui.theme().changed.connect(self._on_theme)

    def _on_theme(self, *_):
        """主题切换后重绘。连接绑定方法而不是 lambda：控件销毁时 Qt 会自动断开。"""
        self.viewport().update()

    def _row(self, proxy_index):
        return self.table_model.rows[self.filter.mapToSource(proxy_index).row()]

    def set_rows(self, rows: list, keep: Callable[[Any], Any] | None = None):
        """keep：用于在刷新后恢复选中的键函数。"""
        prev = keep(self.current_row()) if keep and self.current_row() is not None else None
        self.table_model.set_rows(rows)
        if prev is not None:
            for i, r in enumerate(rows):
                if keep(r) == prev:
                    idx = self.filter.mapFromSource(self.table_model.index(i, 0))
                    if idx.isValid():
                        self.selectRow(idx.row())
                    break

    def current_row(self):
        idx = self.currentIndex()
        if not idx.isValid() or not self.selectionModel().isRowSelected(idx.row(), QtCore.QModelIndex()):
            return None
        return self._row(idx)

    def selected_rows(self) -> list:
        return [self._row(i) for i in self.selectionModel().selectedRows()]

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self.currentIndex().isValid():
            self.rowActivated.emit(self._row(self.currentIndex()))
            return
        super().keyPressEvent(e)


# ── 文本查看器 ────────────────────────────────────────

class TextViewer(W.QWidget):
    """只读等宽文本 + 查找（高亮全部命中，Enter / Shift+Enter 跳转）。toolbar 可再 add 其它控件。"""

    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.toolbar = ui.Toolbar()
        self.find = self.toolbar.add(grow(ui.FilterField("Find", width=240), 120))   # 分栏变窄时可以缩小，不和按钮重叠
        self.prev = self.toolbar.add(ui.icon_button("chevron-up", "上一个（Shift+Enter）"))
        self.next = self.toolbar.add(ui.icon_button("chevron-down", "下一个（Enter）"))
        self.toolbar.separator()
        self.wrap = self.toolbar.add(ui.icon_button("wrap-text", "自动换行", checkable=True))
        self.copy = self.toolbar.add(ui.icon_button("copy", "复制全部"))
        lay.addWidget(self.toolbar)
        self.text = W.QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(ui.theme().mono_font(12))
        self.text.setLineWrapMode(W.QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setPlaceholderText(placeholder)
        ui.repolish(self.text, viewer=True)
        lay.addWidget(self.text, 1)
        self._matches: list[QtGui.QTextCursor] = []
        self._i = -1
        self.find.textChanged.connect(self._search)
        self.find.optionsChanged.connect(self._search)
        self.find.edit.returnPressed.connect(lambda: self._step(-1 if QtWidgets.QApplication.keyboardModifiers() & Qt.KeyboardModifier.ShiftModifier else 1))
        self.prev.clicked.connect(lambda: self._step(-1))
        self.next.clicked.connect(lambda: self._step(1))
        self.wrap.toggled.connect(lambda on: self.text.setLineWrapMode(
            W.QPlainTextEdit.LineWrapMode.WidgetWidth if on else W.QPlainTextEdit.LineWrapMode.NoWrap))
        self.copy.clicked.connect(lambda: QtWidgets.QApplication.clipboard().setText(self.text.toPlainText()))
        sc = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Find, self)
        sc.activated.connect(lambda: (self.find.edit.setFocus(), self.find.edit.selectAll()))
        ui.theme().changed.connect(self._search)

    def set_text(self, text: str):
        self.text.setPlainText(text)
        self._search()

    KIND_TOKEN = {"input": "signal-ink", "output": "ink", "error": "danger", "system": "ink-faint", "ok": "ok"}

    def append(self, text: str, kind: str = "output"):
        """追加一段着色文本（不自动换行）：input 命令行 / output 标准输出 / error 错误输出 / system 提示 / ok 成功。"""
        c = self.text.textCursor()
        c.movePosition(QtGui.QTextCursor.MoveOperation.End)
        f = QtGui.QTextCharFormat()
        f.setForeground(ui.theme().color(self.KIND_TOKEN.get(kind, "ink")))
        if kind == "input":
            f.setFontWeight(QtGui.QFont.Weight.DemiBold)
        c.insertText(text, f)
        self.text.setTextCursor(c)
        self.text.ensureCursorVisible()

    def plain(self) -> str:
        return self.text.toPlainText()

    def _search(self, *_):
        import re
        self._matches = []
        self._i = -1
        q = self.find.text()
        sels = []
        bad = False
        if q:
            o = self.find.options()
            src = q if o["regex"] else re.escape(q)
            if o["wholeWord"]:
                src = r"\b(?:" + src + r")\b"
            try:
                pat = re.compile(src, 0 if o["matchCase"] else re.IGNORECASE)
            except re.error:
                pat, bad = None, True
            if pat:
                doc = self.text.document()
                fmt = QtGui.QTextCharFormat()
                fmt.setBackground(ui.theme().color("log-match"))
                for m in pat.finditer(doc.toPlainText()):
                    if m.end() == m.start():
                        continue
                    c = QtGui.QTextCursor(doc)
                    c.setPosition(m.start())
                    c.setPosition(m.end(), QtGui.QTextCursor.MoveMode.KeepAnchor)
                    self._matches.append(c)
                    if len(sels) < 5000:
                        s = W.QTextEdit.ExtraSelection()
                        s.cursor = c
                        s.format = fmt
                        sels.append(s)
        self.find.set_invalid(bad)
        self.text.setExtraSelections(sels)
        self.find.set_count(len(self._matches) if q else None)
        if self._matches:
            self._step(1)

    def _step(self, d: int):
        if not self._matches:
            return
        self._i = (self._i + d) % len(self._matches)
        c = QtGui.QTextCursor(self._matches[self._i])
        self.text.setTextCursor(c)
        self.text.centerCursor()
        self.find.count.setText(f"{self._i + 1} / {len(self._matches)}")


# ── 长任务 ────────────────────────────────────────────

class TaskStrip(W.QWidget):
    """长任务进度条带：标题、进度、明细、取消。成功后 4 秒自动收起，失败保留到手动关闭。
    margins 是卡片外边距（放在工具栏下方时用 (12, 8, 12, 8)），收起时连同外边距一起隐藏。

        strip.run("正在安装 app.apk", backend.install, serial, paths, opts, done=..., cancellable=True)
    """

    finished = Signal(bool)

    def __init__(self, margins=(0, 0, 0, 0), parent=None):
        super().__init__(parent)
        outer = W.QVBoxLayout(self)
        outer.setContentsMargins(*margins)
        card = W.QFrame()
        ui.repolish(card, card=True)
        outer.addWidget(card)
        lay = W.QHBoxLayout(card)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(12)
        self.row = ui.ProgressRow("", 0)
        lay.addWidget(self.row, 1)
        self.action = ui.button("Cancel", size="sm")
        self.action.clicked.connect(self._on_action)
        lay.addWidget(self.action, 0, Qt.AlignmentFlag.AlignVCenter)
        self._job = None
        self._hide_timer = QtCore.QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)
        self.hide()

    @property
    def busy(self) -> bool:
        return self._job is not None and not self._job.finished

    def run(self, title: str, fn, *args, done=None, error=None, cancellable: bool = True, ok_text: str | None = None, **kw):
        from ..core import jobs
        self._hide_timer.stop()
        self.row.label.setText(title)
        self.row.set_value(None, "", failed=False)
        self.action.setText("Cancel")
        self.action.setVisible(cancellable)
        self.action.setEnabled(True)
        self.show()

        def ok(result):
            self.row.set_value(100, ok_text or "完成")
            self.action.setVisible(False)
            self._hide_timer.start(4000)
            self.finished.emit(True)
            if done:
                done(result)

        def fail(e):
            from ..backend.api import Cancelled
            if isinstance(e, Cancelled):
                self.hide()
            else:
                self.row.set_value(self.row.bar.value() if self.row.bar.maximum() else 100,
                                   str(e) + (f" · {e.detail.strip().splitlines()[-1]}" if getattr(e, "detail", "").strip() else ""),
                                   failed=True)
                self.action.setText("Close")
                self.action.setVisible(True)
            self.finished.emit(False)
            if error:
                error(e)

        self._job = jobs.run(fn, *args, done=ok, error=fail, progress=self._progress, cancellable=cancellable, **kw)
        return self._job

    def _progress(self, p):
        self.row.set_value(p.percent, p.message)

    def _on_action(self):
        if self.busy:
            self._job.cancel()
            self.action.setEnabled(False)
            self.row.set_value(None, "Cancelling…")
        else:
            self.hide()


# ── 浮动提示 ──────────────────────────────────────────

class ToastHost(W.QWidget):
    """叠在窗口右上角的提示栈。show_toast(tone, title, body)；danger 停留 8 秒，其余 4 秒。"""

    def __init__(self, parent: QtWidgets.QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self._lay = W.QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(8)
        self._lay.addStretch(1)
        self.setFixedWidth(380)
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, ev):
        if obj is self.parent() and ev.type() == QtCore.QEvent.Type.Resize:
            self._place()
        return False

    def _place(self):
        """按固定宽度计算每条提示换行后的高度再求和（sizeHint 不考虑换行，多条叠加时会被压扁）。"""
        p = self.parentWidget()
        w = self.width()
        banners = [self._lay.itemAt(i).widget() for i in range(self._lay.count()) if self._lay.itemAt(i).widget()]
        h = sum(b.heightForWidth(w) if b.hasHeightForWidth() else b.sizeHint().height() for b in banners)
        h += self._lay.spacing() * max(0, len(banners) - 1)
        self.setGeometry(p.width() - w - 16, 56, w, h)

    def show_toast(self, tone: str, title: str, body: str = ""):
        b = ui.Banner(tone if tone in ui.Banner.ICONS else "info", title, body.strip(), dismissible=True)
        ui.repolish(b, toast=True)
        shadow = W.QGraphicsDropShadowEffect(b)
        shadow.setBlurRadius(24)
        shadow.setOffset(0, 8)
        shadow.setColor(QtGui.QColor(0, 0, 0, 110))
        b.setGraphicsEffect(shadow)
        self._lay.insertWidget(self._lay.count() - 1, b)
        while self._lay.count() > 5:  # 最多 4 条
            old = self._lay.itemAt(0).widget()
            old.setParent(None)
            old.deleteLater()
        b.dismissed.connect(lambda: self._remove(b))
        QtCore.QTimer.singleShot(8000 if tone == "danger" else 4000, lambda: self._remove(b))
        self.show()
        self.raise_()
        self._place()

    def _remove(self, b):
        try:
            b.setParent(None)
            b.deleteLater()
        except RuntimeError:
            return
        if self._lay.count() <= 1:
            self.hide()
        else:
            self._place()


# ── 对话框 ────────────────────────────────────────────

class Dialog(W.QDialog):
    """与 tbui.confirm 同款外观的表单对话框：标题、正文区（self.body 布局）、底栏按钮。"""

    def __init__(self, parent, title: str, ok_text: str = "确定", danger: bool = False, width: int = 480,
                 cancel_text: str = "取消"):
        super().__init__(parent)
        self.setWindowTitle(title)
        ui.repolish(self, tb=True)
        self.setMinimumWidth(width)
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        box = W.QWidget()
        self.body = W.QVBoxLayout(box)
        self.body.setContentsMargins(20, 20, 20, 20)
        self.body.setSpacing(12)
        head = W.QHBoxLayout()
        head.setSpacing(8)
        if danger:
            ic = W.QLabel()
            ic.setPixmap(ui.theme().pixmap("triangle-alert", "danger", 20))
            head.addWidget(ic)
        t = W.QLabel(title)
        t.setFont(ui.theme().display_font(18))
        head.addWidget(t, 1)
        self.body.addLayout(head)
        lay.addWidget(box, 1)
        foot = W.QFrame()
        ui.repolish(foot, dialogfooter=True)
        fl = W.QHBoxLayout(foot)
        fl.setContentsMargins(20, 16, 20, 16)
        fl.setSpacing(8)
        self.footer = fl
        fl.addStretch(1)
        self.cancel_btn = ui.button(cancel_text, size="lg")
        self.ok_btn = ui.button(ok_text, "danger" if danger else "primary", "lg")
        for b in (self.cancel_btn, self.ok_btn):
            b.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            fl.addWidget(b)
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn.clicked.connect(self._try_accept)
        (self.cancel_btn if danger else self.ok_btn).setDefault(True)
        lay.addWidget(foot)
        self.validate: Callable[[], str | None] | None = None  # 返回错误文字则不关闭

    def add_text(self, text: str, muted: bool = True):
        l = W.QLabel(text)
        l.setWordWrap(True)
        l.setTextFormat(Qt.TextFormat.RichText)
        if muted:
            ui.repolish(l, dialogbody=True)
        self.body.addWidget(l)
        return l

    def add(self, w):
        self.body.addWidget(w)
        return w

    def _try_accept(self):
        if self.validate:
            err = self.validate()
            if err:
                return
        self.accept()

    def run(self) -> bool:
        return self.exec() == W.QDialog.DialogCode.Accepted


def ask_text(parent, title: str, field_label: str, value: str = "", placeholder: str = "", hint: str = "",
             ok_text: str = "确定", mono: bool = True, check: Callable[[str], str | None] | None = None) -> str | None:
    """单行输入对话框；check 返回错误文字时阻止关闭。"""
    d = Dialog(parent, title, ok_text)
    f = ui.TextField(field_label, placeholder, hint, mono=mono)
    f.edit.setText(value)
    f.edit.selectAll()
    d.add(f)

    def validate():
        err = check(f.edit.text()) if check else (None if f.edit.text().strip() else "不能为空")
        f.set_error(err)
        return err
    d.validate = validate
    f.edit.setFocus()
    return f.edit.text().strip() if d.run() else None


def open_files(parent, title: str, filters: str = "所有文件 (*)", multi: bool = False, start: str = "") -> list[str]:
    if multi:
        paths, _ = W.QFileDialog.getOpenFileNames(parent, title, start, filters)
        return paths
    path, _ = W.QFileDialog.getOpenFileName(parent, title, start, filters)
    return [path] if path else []


def pick_dir(parent, title: str, start: str = "") -> str | None:
    return W.QFileDialog.getExistingDirectory(parent, title, start) or None


def reveal(path: str):
    """在资源管理器中显示文件。"""
    import os
    import subprocess
    p = os.path.normpath(path)
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", p] if os.path.isfile(p) else ["explorer", p])
    else:
        QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(os.path.dirname(p) if os.path.isfile(p) else p))


# ── 紧凑控件 ──────────────────────────────────────────
# 约定：工具栏控件一律 24px 高（sm）；文字用英文或通用缩写，中文说明放在 tooltip；能用图标按钮就不用文字按钮。

def narrow_select(items, value=None, width: int = 80, tip: str = "", mono: bool = True) -> QtWidgets.QComboBox:
    """窄下拉。items 为 [(值, 显示文字)] 或 [(值, 显示文字, 中文说明)]，说明作为每项的 tooltip。"""
    c = ui.select([(it[0], it[1]) for it in items], value, size="sm", mono=mono)
    for i, it in enumerate(items):
        if len(it) > 2 and it[2]:
            c.setItemData(i, it[2], Qt.ItemDataRole.ToolTipRole)
    c.setFixedWidth(width)
    ui.repolish(c, narrow=True)          # 缩小右侧箭头区域，见 app.qss
    c.view().setMinimumWidth(width + 24)
    if tip:
        c.setToolTip(tip)
    return c


def grow(w: QtWidgets.QWidget, min_width: int = 240) -> QtWidgets.QWidget:
    """让过滤框等主输入占满工具栏剩余宽度（不再需要 Toolbar.stretch）。"""
    w.setMinimumWidth(min_width)
    w.setMaximumWidth(16777215)
    w.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Fixed)
    return w


def tbtn(text: str, tip: str = "", variant: str = "secondary", icon: str | None = None, checkable: bool = False,
         width: int | None = None, menu=None) -> QtWidgets.QPushButton:
    """工具栏文字按钮：sm 尺寸、英文短文字、中文 tooltip。"""
    b = ui.button(text, variant, "sm", icon=icon, menu=menu)
    b.setCheckable(checkable)
    if tip:
        b.setToolTip(tip)
    if width:
        b.setFixedWidth(width)
    return b


# ── 其它 ──────────────────────────────────────────────

def output_log(min_height: int = 120) -> "ui.Terminal":
    """只读的命令输出区（Terminal 去掉输入行）。write(text, kind) 中 kind = input / output / error / system。"""
    t = ui.Terminal("$")
    t.input.parentWidget().hide()
    t.setMinimumHeight(min_height)
    return t


class SegmentStack(W.QWidget):
    """分段按钮切换的多页区域：header 里放分段按钮与右侧操作，下面是页面栈。"""

    changed = Signal(str)

    def __init__(self, pages: list[tuple[str, str, QtWidgets.QWidget]], value: str | None = None, parent=None):
        super().__init__(parent)
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.header = ui.Toolbar()
        self.seg = self.header.add(ui.SegmentedControl([(k, t) for k, t, _ in pages], value or pages[0][0], label="视图"))
        lay.addWidget(self.header)
        self.stack = W.QStackedWidget()
        self._pages = {}
        for k, _, w in pages:
            self._pages[k] = w
            self.stack.addWidget(w)
        lay.addWidget(self.stack, 1)
        self.seg.changed.connect(self._show)
        self._show(self.seg.value())

    def _show(self, key: str):
        self.stack.setCurrentWidget(self._pages[key])
        self.changed.emit(key)

    def value(self) -> str:
        return self.seg.value()

    def page(self, key: str) -> QtWidgets.QWidget:
        return self._pages[key]
