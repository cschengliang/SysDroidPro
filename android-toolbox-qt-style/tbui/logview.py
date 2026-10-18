"""Logcat 日志视图：模型、过滤代理、自绘 delegate 和组装好的 LogView。

    view = LogView()
    view.log_model.append_rows([LogRow(...), ...])  # 在主线程调用（adb 读取线程用信号把行送过来）；内部按 80ms 批量插入
    view.set_filter(text="OkHttp", min_level="D", regex=False, match_case=False, whole_word=False)
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ._qt import QtCore, QtGui, QtWidgets, Qt, Signal
from .theme import theme, repolish

W = QtWidgets
LEVELS = "VDIWEFA"
LEVEL_TOKEN = {"V": "log-verbose", "D": "log-debug", "I": "log-info", "W": "log-warn", "E": "log-error", "F": "log-fatal", "A": "log-fatal"}
LEVEL_SOFT = {"V": "bg-400", "D": "info-soft", "I": "ok-soft", "W": "warn-soft", "E": "danger-soft", "F": "special-soft", "A": "special-soft"}
MSG_TOKEN = {"V": "log-verbose", "W": "log-warn", "E": "log-error", "F": "log-fatal", "A": "log-fatal"}
COLUMNS = ["时间", "PID", "TID", "", "Tag", "消息"]
COL_TIME, COL_PID, COL_TID, COL_LEVEL, COL_TAG, COL_MSG = range(6)

# threadtime 格式：10-18 13:16:02.481  1532  1588 I ActivityManager: Start proc ...
THREADTIME = re.compile(r"^(\d\d-\d\d \d\d:\d\d:\d\d\.\d{3})\s+(\d+)\s+(\d+)\s+([VDIWEFA])\s+(.*?)\s*: (.*)$")


@dataclass(slots=True)
class LogRow:
    time: str
    pid: int
    tid: int
    level: str
    tag: str
    message: str

    @staticmethod
    def parse(line: str) -> "LogRow | None":
        m = THREADTIME.match(line.rstrip("\r\n"))
        if not m:
            return None
        t, pid, tid, lvl, tag, msg = m.groups()
        return LogRow(t, int(pid), int(tid), lvl, tag, msg)


class LogModel(QtCore.QAbstractTableModel):
    """只追加的日志模型。append_rows 先进缓冲区，定时批量插入，避免逐行刷新拖慢界面。"""

    def __init__(self, max_rows: int = 200_000, parent=None):
        super().__init__(parent)
        self.rows: list[LogRow] = []
        self.max_rows = max_rows
        self._pending: list[LogRow] = []
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._flush)
        self._timer.start()

    def rowCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent=QtCore.QModelIndex()):
        return 0 if parent.isValid() else len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return COLUMNS[section]
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.TextAlignmentRole and section in (COL_PID, COL_TID):
            return int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return None

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        r = self.rows[index.row()]
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            if role == Qt.ItemDataRole.ToolTipRole and index.column() not in (COL_TAG, COL_MSG):
                return None
            return (r.time, str(r.pid), str(r.tid), r.level, r.tag, r.message)[index.column()]
        if role == Qt.ItemDataRole.UserRole:
            return r
        return None

    def append_rows(self, rows):
        self._pending.extend(rows)

    def clear(self):
        self.beginResetModel()
        self.rows.clear()
        self._pending.clear()
        self.endResetModel()

    def _flush(self):
        if not self._pending:
            return
        batch, self._pending = self._pending, []
        overflow = len(self.rows) + len(batch) - self.max_rows
        if overflow > 0:
            drop = min(overflow, len(self.rows))
            self.beginRemoveRows(QtCore.QModelIndex(), 0, drop - 1)
            del self.rows[:drop]
            self.endRemoveRows()
            batch = batch[-self.max_rows:]
        start = len(self.rows)
        self.beginInsertRows(QtCore.QModelIndex(), start, start + len(batch) - 1)
        self.rows.extend(batch)
        self.endInsertRows()


class LogFilterProxy(QtCore.QSortFilterProxyModel):
    """按最低级别和文字（tag + 消息）过滤；支持大小写、全字、正则。"""

    invalidPattern = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.min_level = "V"
        self.pids: set[int] | None = None     # 只显示这些进程的日志；None 表示不按进程过滤
        self._re: re.Pattern | None = None
        self.highlight: re.Pattern | None = None

    def set_filter(self, text: str = "", min_level: str = "V", regex: bool = False,
                   match_case: bool = False, whole_word: bool = False, pids: set[int] | None = None):
        new_api = hasattr(self, "beginFilterChange")  # Qt 6.10+ 弃用了 invalidateFilter
        if new_api:
            self.beginFilterChange()
        self.min_level = min_level
        self.pids = pids or None
        bad = False
        pat = None
        if text:
            src = text if regex else re.escape(text)
            if whole_word:
                src = r"\b(?:" + src + r")\b"
            try:
                pat = re.compile(src, 0 if match_case else re.IGNORECASE)
            except re.error:
                bad = True
                pat = self._re  # 正则写错时保留上一次有效的过滤
        self._re = pat
        self.highlight = pat
        if new_api:
            self.endFilterChange(QtCore.QSortFilterProxyModel.Direction.Rows)
        else:
            self.invalidateFilter()
        self.invalidPattern.emit(bad)

    def filterAcceptsRow(self, row, parent):
        r: LogRow = self.sourceModel().rows[row]
        if LEVELS.index(r.level) < LEVELS.index(self.min_level):
            return False
        if self.pids is not None and r.pid not in self.pids:
            return False
        return self._re is None or bool(self._re.search(r.tag) or self._re.search(r.message))


class LogDelegate(W.QStyledItemDelegate):
    """绘制一行日志的单元格：等宽字体、级别字母块、按级别着色、搜索命中高亮。"""

    def __init__(self, view: "LogView"):
        super().__init__(view)
        self.view = view

    def paint(self, p: QtGui.QPainter, option, index):
        t = theme()
        r: LogRow = index.data(Qt.ItemDataRole.UserRole)
        col = index.column()
        rect = QtCore.QRectF(option.rect)
        S = W.QStyle.StateFlag
        p.save()
        if option.state & S.State_Selected:
            p.fillRect(rect, t.color("bg-500"))
        elif index.row() == self.view.hover_row:
            p.fillRect(rect, t.color("bg-400"))
        font = self.view.mono
        p.setFont(font)
        fm = QtGui.QFontMetrics(font)
        pad_l = 12 if col == COL_TIME else 4
        pad_r = 4
        box = rect.adjusted(pad_l, 0, -pad_r, 0)
        if col == COL_LEVEL:
            p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            b = QtCore.QRectF(box.left(), rect.center().y() - 8, 16, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(t.color(LEVEL_SOFT[r.level]))
            p.drawRoundedRect(b, 2, 2)
            p.setFont(self.view.mono_bold)
            p.setPen(t.color(LEVEL_TOKEN[r.level]))
            p.drawText(b, Qt.AlignmentFlag.AlignCenter, r.level)
            p.restore()
            return
        if col in (COL_TIME, COL_PID, COL_TID):
            color = t.color("ink-faint")
        elif col == COL_TAG:
            color = t.color("ink-muted")
        else:
            color = t.color(MSG_TOKEN.get(r.level, "ink"))
        text = index.data(Qt.ItemDataRole.DisplayRole).replace("\t", "    ")
        align = Qt.AlignmentFlag.AlignVCenter | (Qt.AlignmentFlag.AlignRight if col in (COL_PID, COL_TID) else Qt.AlignmentFlag.AlignLeft)
        elided = fm.elidedText(text, Qt.TextElideMode.ElideRight, int(box.width()))
        pat = self.view.proxy.highlight
        if pat is not None and col in (COL_TAG, COL_MSG):
            for m in pat.finditer(elided):
                if m.end() == m.start():
                    continue
                x0 = box.left() + fm.horizontalAdvance(elided[:m.start()])
                w = fm.horizontalAdvance(elided[m.start():m.end()])
                p.fillRect(QtCore.QRectF(x0, rect.top() + 1, w, rect.height() - 2), t.color("log-match"))
        p.setPen(color)
        p.drawText(box, align, elided)
        if pat is not None and col in (COL_TAG, COL_MSG):  # 命中文字用 ink 重绘，保证在 log-match 上可读
            p.setPen(t.color("ink"))
            for m in pat.finditer(elided):
                if m.end() == m.start():
                    continue
                x0 = box.left() + fm.horizontalAdvance(elided[:m.start()])
                p.drawText(QtCore.QRectF(x0, box.top(), box.width(), box.height()),
                           Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided[m.start():m.end()])
        p.restore()


class LogView(W.QTableView):
    """组装好的 Logcat 表：行高 20、列宽固定、跟随最新。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        t = theme()
        self.mono = t.mono_font(12)
        self.mono_bold = t.mono_font(11, 600)
        self.log_model = LogModel(parent=self)
        self.proxy = LogFilterProxy(self)
        self.proxy.setSourceModel(self.log_model)
        self.setModel(self.proxy)
        self.setItemDelegate(LogDelegate(self))
        repolish(self, log=True)
        self.hover_row = -1
        self.follow = True
        self.setMouseTracking(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(W.QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setVerticalScrollMode(W.QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        vh = self.verticalHeader()
        vh.hide()
        vh.setSectionResizeMode(W.QHeaderView.ResizeMode.Fixed)
        vh.setDefaultSectionSize(20)
        hh = self.horizontalHeader()
        hh.setHighlightSections(False)
        hh.setSectionsClickable(False)
        hh.setFixedHeight(24)
        hh.setFont(t.label_font())
        fm = QtGui.QFontMetrics(self.mono)
        ch = fm.horizontalAdvance("0")
        widths = {COL_TIME: 18 * ch + 20, COL_PID: 5 * ch + 8, COL_TID: 5 * ch + 8, COL_LEVEL: 24, COL_TAG: 22 * ch + 8}
        for c, w in widths.items():
            hh.setSectionResizeMode(c, W.QHeaderView.ResizeMode.Interactive)
            hh.resizeSection(c, int(w))
        hh.setSectionResizeMode(COL_LEVEL, W.QHeaderView.ResizeMode.Fixed)
        hh.setStretchLastSection(True)
        self.log_model.rowsInserted.connect(self._maybe_follow)
        t.changed.connect(self._on_theme)

    def _on_theme(self, *_):
        """主题切换后重绘。连接绑定方法而不是 lambda：控件销毁时 Qt 会自动断开。"""
        self.viewport().update()

    def set_filter(self, **kw):
        self.proxy.set_filter(**kw)
        self.viewport().update()

    def set_follow(self, on: bool):
        self.follow = on
        if on:
            self.scrollToBottom()

    def _maybe_follow(self, *_):
        if self.follow:
            QtCore.QTimer.singleShot(0, self.scrollToBottom)

    def mouseMoveEvent(self, e):
        row = self.rowAt(int(e.position().y())) if hasattr(e, "position") else self.rowAt(e.pos().y())
        if row != self.hover_row:
            self.hover_row = row
            self.viewport().update()
        super().mouseMoveEvent(e)

    def leaveEvent(self, e):
        self.hover_row = -1
        self.viewport().update()
        super().leaveEvent(e)

    def wheelEvent(self, e):
        super().wheelEvent(e)
        sb = self.verticalScrollBar()
        if e.angleDelta().y() > 0 and self.follow:  # 向上滚动时自动停止跟随
            self.follow = False
            self.followChanged.emit(False)
        elif sb.value() == sb.maximum() and not self.follow:
            self.follow = True
            self.followChanged.emit(True)

    followChanged = Signal(bool)
