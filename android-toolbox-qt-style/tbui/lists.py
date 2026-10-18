"""自绘列表：侧边导航 Sidebar、设备列表 DeviceList。"""
from __future__ import annotations

from ._qt import QtCore, QtGui, QtWidgets, Qt, Signal
from .theme import theme, repolish
from .widgets import STATE_TEXT

W = QtWidgets
ROLE_KIND = Qt.ItemDataRole.UserRole + 1   # "group" / "item"
ROLE_ID = Qt.ItemDataRole.UserRole + 2
ROLE_ICON = Qt.ItemDataRole.UserRole + 3
ROLE_BADGE = Qt.ItemDataRole.UserRole + 4
ROLE_DEVICE = Qt.ItemDataRole.UserRole + 5


def _rounded(p: QtGui.QPainter, rect: QtCore.QRectF, r: float, fill=None, pen=None):
    p.setPen(pen if pen is not None else Qt.PenStyle.NoPen)
    p.setBrush(fill if fill is not None else Qt.BrushStyle.NoBrush)
    p.drawRoundedRect(rect, r, r)


# ── Sidebar ───────────────────────────────────────────

class _SidebarDelegate(W.QStyledItemDelegate):
    def __init__(self, view):
        super().__init__(view)
        self.view = view

    def sizeHint(self, option, index):
        if index.data(ROLE_KIND) == "group":
            first = index.row() == 0
            return QtCore.QSize(0, 20 if first else 32) if not self.view.collapsed else QtCore.QSize(0, 9 if not first else 0)
        return QtCore.QSize(0, 30)  # 28 + 2 间距

    def paint(self, p: QtGui.QPainter, option, index):
        t = theme()
        p.save()
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        r = QtCore.QRectF(option.rect)
        collapsed = self.view.collapsed
        if index.data(ROLE_KIND) == "group":
            if collapsed:
                if index.row() > 0:
                    p.fillRect(QtCore.QRectF(r.left() + 10, r.center().y(), r.width() - 20, 1), t.color("line"))
            else:
                p.setFont(t.label_font())
                p.setPen(t.color("ink-faint"))
                p.drawText(r.adjusted(16, 0, -8, -4), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, index.data(Qt.ItemDataRole.DisplayRole))
            p.restore()
            return
        S = W.QStyle.StateFlag
        selected = bool(option.state & S.State_Selected)
        hover = bool(option.state & S.State_MouseOver)
        item = QtCore.QRectF(r.left() + 8, r.top(), r.width() - 16, 28)
        if collapsed:
            item = QtCore.QRectF(r.left() + 6, r.top(), r.width() - 12, 28)
        if selected:
            _rounded(p, item, 4, t.color("signal-soft"))
            _rounded(p, QtCore.QRectF(item.left(), item.top() + 7, 3, 14), 1.5, t.color("signal-ink"))
        elif hover:
            _rounded(p, item, 4, t.color("bg-400"))
        icon_color = "signal-ink" if selected else ("ink" if hover else "ink-muted")
        pm = t.pixmap(index.data(ROLE_ICON), icon_color, 16)
        ix = item.center().x() - 8 if collapsed else item.left() + 10
        p.drawPixmap(QtCore.QPointF(ix, item.top() + 6), pm)
        badge = index.data(ROLE_BADGE)
        if not collapsed:
            p.setFont(t.ui_font(13, 600 if selected else 400))
            p.setPen(t.color("ink") if (selected or hover) else t.color("ink-muted"))
            text_r = item.adjusted(36, 0, -8, 0)
            if badge not in (None, ""):
                text_r.setRight(text_r.right() - 28)
            p.drawText(text_r, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                       p.fontMetrics().elidedText(index.data(Qt.ItemDataRole.DisplayRole), Qt.TextElideMode.ElideRight, int(text_r.width())))
        if badge not in (None, ""):
            f = t.mono_font(11 if not collapsed else 10, 500)
            p.setFont(f)
            w = max(16, QtGui.QFontMetrics(f).horizontalAdvance(str(badge)) + 10)
            h = 16 if not collapsed else 14
            br = QtCore.QRectF(item.right() - w - 8, item.top() + 6, w, h) if not collapsed else QtCore.QRectF(item.right() - w + 1, item.top() + 1, w, h)
            _rounded(p, br, h / 2, t.color("bg-500"))
            p.setPen(t.color("ink-muted"))
            p.drawText(br, Qt.AlignmentFlag.AlignCenter, str(badge))
        p.restore()


class Sidebar(W.QListWidget):
    """左侧工具导航。add_group("调试") / add_item("logcat", "Logcat 日志", "scroll-text", badge=None)。currentChanged 发出条目 id。"""

    currentChanged = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.collapsed = False
        repolish(self, sidebar=True, delegate=True)
        self.setItemDelegate(_SidebarDelegate(self))
        self.setMouseTracking(True)
        self.setFixedWidth(200)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSpacing(0)
        self.setViewportMargins(0, 8, 0, 0)
        self.currentItemChanged.connect(self._emit)
        theme().changed.connect(self._on_theme)

    def _on_theme(self, *_):
        """主题切换后重绘。连接绑定方法而不是 lambda：控件销毁时 Qt 会自动断开。"""
        self.viewport().update()

    def add_group(self, label: str):
        it = W.QListWidgetItem(label)
        it.setData(ROLE_KIND, "group")
        it.setFlags(Qt.ItemFlag.NoItemFlags)
        self.addItem(it)

    def add_item(self, id_: str, label: str, icon: str, badge=None):
        it = W.QListWidgetItem(label)
        it.setData(ROLE_KIND, "item")
        it.setData(ROLE_ID, id_)
        it.setData(ROLE_ICON, icon)
        it.setData(ROLE_BADGE, badge)
        it.setToolTip(label)
        self.addItem(it)
        return it

    def set_badge(self, id_: str, badge):
        for i in range(self.count()):
            it = self.item(i)
            if it.data(ROLE_ID) == id_:
                it.setData(ROLE_BADGE, badge)

    def select(self, id_: str):
        for i in range(self.count()):
            if self.item(i).data(ROLE_ID) == id_:
                self.setCurrentRow(i)

    def set_collapsed(self, on: bool):
        self.collapsed = on
        self.setFixedWidth(48 if on else 200)
        self.doItemsLayout()

    def _emit(self, cur, _prev):
        if cur is not None and cur.data(ROLE_KIND) == "item":
            self.currentChanged.emit(cur.data(ROLE_ID))


# ── DeviceList ────────────────────────────────────────

class _DeviceDelegate(W.QStyledItemDelegate):
    H = 76

    def sizeHint(self, option, index):
        return QtCore.QSize(0, self.H + 2)

    def paint(self, p: QtGui.QPainter, option, index):
        t = theme()
        d = index.data(ROLE_DEVICE) or {}
        S = W.QStyle.StateFlag
        selected = bool(option.state & S.State_Selected)
        hover = bool(option.state & S.State_MouseOver)
        p.save()
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        card = QtCore.QRectF(option.rect).adjusted(8.5, 0.5, -8.5, -2.5)
        if selected:
            _rounded(p, card, 6, t.color("signal-soft"), QtGui.QPen(t.color("signal-ink"), 1))
        elif hover:
            _rounded(p, card, 6, t.color("bg-400"))
        # 图标块
        ib = QtCore.QRectF(card.left() + 12, card.top() + 10, 32, 32)
        _rounded(p, ib, 4, t.color("bg-100" if selected else "bg-400"))
        icon = "monitor-smartphone" if d.get("transport") == "emulator" else "smartphone"
        p.drawPixmap(QtCore.QPointF(ib.left() + 6, ib.top() + 6), t.pixmap(icon, "signal-ink" if selected else "ink-muted", 20))
        x = ib.right() + 12
        right = card.right() - 12
        # 状态（右上）
        text, tone = STATE_TEXT.get(d.get("state", "offline"), STATE_TEXT["offline"])
        f12 = t.ui_font(12)
        p.setFont(f12)
        sw = QtGui.QFontMetrics(f12).horizontalAdvance(text)
        sx = right - sw
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(t.color(tone))
        p.drawEllipse(QtCore.QRectF(sx - 14, card.top() + 16, 8, 8))
        p.setPen(t.color(tone))
        p.drawText(QtCore.QRectF(sx, card.top() + 10, sw + 1, 20), Qt.AlignmentFlag.AlignVCenter, text)
        # 型号
        maker = d.get("manufacturer") or ""
        model = d.get("model", "")
        top = QtCore.QRectF(x, card.top() + 10, sx - 20 - x, 20)
        fm_reg = t.ui_font(13)
        fm_bold = t.ui_font(13, 600)
        if maker:
            p.setFont(fm_reg)
            p.setPen(t.color("ink-muted"))
            mw = QtGui.QFontMetrics(fm_reg).horizontalAdvance(maker + " ")
            p.drawText(top, Qt.AlignmentFlag.AlignVCenter, maker + " ")
            top.setLeft(top.left() + mw)
        p.setFont(fm_bold)
        p.setPen(t.color("ink"))
        p.drawText(top, Qt.AlignmentFlag.AlignVCenter,
                   QtGui.QFontMetrics(fm_bold).elidedText(model, Qt.TextElideMode.ElideRight, int(top.width())))
        # 序列号
        mono = t.mono_font(12)
        p.setFont(mono)
        p.setPen(t.color("ink-muted"))
        p.drawText(QtCore.QRectF(x, card.top() + 32, right - x, 16), Qt.AlignmentFlag.AlignVCenter,
                   QtGui.QFontMetrics(mono).elidedText(d.get("serial", ""), Qt.TextElideMode.ElideRight, int(right - x)))
        # 元数据
        tr = {"usb": ("usb", "USB"), "wifi": ("wifi", "Wi-Fi"), "emulator": ("monitor-smartphone", "模拟器")}.get(d.get("transport", "usb"))
        p.drawPixmap(QtCore.QPointF(x, card.top() + 52), t.pixmap(tr[0], "ink-muted", 12))
        parts = [tr[1]]
        if d.get("android"):
            parts.append("Android " + str(d["android"]))
        if d.get("api"):
            parts.append("API " + str(d["api"]))
        if d.get("battery") is not None:
            parts.append(f"{d['battery']}%")
        p.setFont(f12)
        p.setPen(t.color("ink-muted"))
        p.drawText(QtCore.QRectF(x + 16, card.top() + 50, right - x - 16, 16), Qt.AlignmentFlag.AlignVCenter, "  ·  ".join(parts))
        p.restore()


class DeviceList(W.QListWidget):
    """设备栏。set_devices([{model, serial, manufacturer?, android?, api?, transport, state, battery?}])；deviceSelected 发出 serial。"""

    deviceSelected = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        repolish(self, devices=True, delegate=True)
        self.setItemDelegate(_DeviceDelegate(self))
        self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setViewportMargins(0, 8, 0, 8)
        self.currentItemChanged.connect(lambda cur, _p: cur and self.deviceSelected.emit(cur.data(ROLE_DEVICE)["serial"]))
        theme().changed.connect(self._on_theme)

    def _on_theme(self, *_):
        """主题切换后重绘。连接绑定方法而不是 lambda：控件销毁时 Qt 会自动断开。"""
        self.viewport().update()

    def set_devices(self, devices: list[dict]):
        keep = self.currentItem().data(ROLE_DEVICE)["serial"] if self.currentItem() else None
        self.blockSignals(True)
        self.clear()
        for d in devices:
            it = W.QListWidgetItem()
            it.setData(ROLE_DEVICE, d)
            it.setToolTip(f"{d.get('manufacturer', '')} {d.get('model', '')}\n{d.get('serial', '')}".strip())
            self.addItem(it)
            if d.get("serial") == keep:
                self.setCurrentItem(it)
        self.blockSignals(False)
        if self.currentItem() is None and self.count():
            self.setCurrentRow(0)
