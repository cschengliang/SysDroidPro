"""macOS 风格一体化玻璃顶栏与三色窗口控制按钮（关闭 / 最小化 / 最大化）。

搭配 backdrop.py 的 Win32 WM_NCCALCSIZE / WM_NCHITTEST 使用：既实现无边框一体化玻璃顶栏，
又完整保留 Win11 Acrylic 毛玻璃、圆角、阴影、8 向拉伸缩放与 Aero Snap 分屏。
"""
from __future__ import annotations

from ._qt import QtCore, QtGui, QtWidgets, Qt
from .theme import theme, repolish

W = QtWidgets

# macOS Sonoma / Sequoia 经典三色规格：(键名, 填充色, 描边色, 符号色)
_LIGHTS = (
    ("close", "#ff5f57", "#e0443e", "#4d0000"),
    ("min",   "#febc2e", "#d89e24", "#5c3d00"),
    ("max",   "#28c840", "#1aab29", "#004d0d"),
)
_RADIUS = 6.0
_GAP = 8.0
_DIAM = _RADIUS * 2.0


class MacTrafficLights(W.QWidget):
    """左侧 macOS 三色窗口按钮：悬停时三圆点同时浮现符号，失焦时柔和退色。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(58, 28)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._hovered = False
        self._hover_idx = -1
        self._pressed_idx = -1
        self._win = None
        theme().changed.connect(self._on_theme)

    def _on_theme(self, *_):
        self.update()

    def showEvent(self, e):
        super().showEvent(e)
        win = self.window()
        if win is not None and win is not self._win:
            if self._win is not None:
                try:
                    self._win.removeEventFilter(self)
                except RuntimeError:
                    pass
            self._win = win
            win.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if obj is self._win and ev.type() in (
            QtCore.QEvent.Type.WindowActivate,
            QtCore.QEvent.Type.WindowDeactivate,
            QtCore.QEvent.Type.WindowStateChange,
        ):
            self.update()
        return super().eventFilter(obj, ev)

    def _circle_center(self, idx: int) -> QtCore.QPointF:
        total_w = 3 * _DIAM + 2 * _GAP
        start_x = (self.width() - total_w) / 2.0 + _RADIUS
        return QtCore.QPointF(start_x + idx * (_DIAM + _GAP), self.height() / 2.0)

    def _hit(self, pos: QtCore.QPointF) -> int:
        for i in range(3):
            c = self._circle_center(i)
            dx = pos.x() - c.x()
            dy = pos.y() - c.y()
            if dx * dx + dy * dy <= (_RADIUS + 3.0) ** 2:
                return i
        return -1

    def _update_tooltip(self, idx: int):
        win = self.window()
        zoomed = bool(win and win.isMaximized())
        tips = ("关闭", "最小化", "还原" if zoomed else "最大化")
        self.setToolTip(tips[idx] if 0 <= idx < 3 else "")

    def enterEvent(self, e):
        self._hovered = True
        pos = e.position() if hasattr(e, "position") else QtCore.QPointF(e.pos())
        self._hover_idx = self._hit(pos)
        self._update_tooltip(self._hover_idx)
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._hover_idx = -1
        self._pressed_idx = -1
        self.setToolTip("")
        self.update()
        super().leaveEvent(e)

    def mouseMoveEvent(self, e):
        pos = e.position() if hasattr(e, "position") else QtCore.QPointF(e.pos())
        idx = self._hit(pos)
        if idx != self._hover_idx:
            self._hover_idx = idx
            self._update_tooltip(idx)
            self.update()
        super().mouseMoveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position() if hasattr(e, "position") else QtCore.QPointF(e.pos())
            self._pressed_idx = self._hit(pos)
            self.update()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            pos = e.position() if hasattr(e, "position") else QtCore.QPointF(e.pos())
            idx = self._hit(pos)
            pressed = self._pressed_idx
            self._pressed_idx = -1
            self.update()
            if idx != -1 and idx == pressed:
                self._trigger(idx)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def _trigger(self, idx: int):
        win = self.window()
        if win is None:
            return
        if idx == 0:
            win.close()
        elif idx == 1:
            win.showMinimized()
        elif idx == 2:
            if win.isMaximized():
                win.showNormal()
            else:
                win.showMaximized()

    def paintEvent(self, _):
        t = theme()
        win = self.window()
        active = True if win is None else win.isActiveWindow()
        dark = t.name == "dark"
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)

        for idx, (kind, fill_hex, edge_hex, sym_hex) in enumerate(_LIGHTS):
            c = self._circle_center(idx)
            rect = QtCore.QRectF(c.x() - _RADIUS, c.y() - _RADIUS, _DIAM, _DIAM)

            if not active and not self._hovered:
                fill = QtGui.QColor(255, 255, 255, 42) if dark else QtGui.QColor(0, 0, 0, 38)
                edge = QtGui.QColor(255, 255, 255, 28) if dark else QtGui.QColor(0, 0, 0, 26)
            else:
                fill = QtGui.QColor(fill_hex)
                edge = QtGui.QColor(edge_hex)
                if self._pressed_idx == idx:
                    fill = fill.darker(118)

            p.setPen(QtGui.QPen(edge, 1.0))
            p.setBrush(fill)
            p.drawEllipse(rect)

            # 顶部极细镜面反光弧
            if active or self._hovered:
                hi = QtGui.QColor(255, 255, 255, 95)
                p.setPen(QtGui.QPen(hi, 0.9, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(rect.adjusted(1.3, 1.1, -1.3, -1.3), 35 * 16, 110 * 16)

            # 鼠标进入三色区时，三颗按钮同步显示矢量符号
            if self._hovered:
                sym = QtGui.QColor(sym_hex)
                sym.setAlpha(215 if self._hover_idx == idx else 165)
                pen = QtGui.QPen(sym, 1.35, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
                p.setPen(pen)
                cx, cy = c.x(), c.y()
                if kind == "close":
                    d = 2.65
                    p.drawLine(QtCore.QPointF(cx - d, cy - d), QtCore.QPointF(cx + d, cy + d))
                    p.drawLine(QtCore.QPointF(cx + d, cy - d), QtCore.QPointF(cx - d, cy + d))
                elif kind == "min":
                    p.drawLine(QtCore.QPointF(cx - 3.1, cy), QtCore.QPointF(cx + 3.1, cy))
                elif kind == "max":
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(sym)
                    if win is not None and win.isMaximized():
                        # 还原态：两个内指小三角
                        tri1 = QtGui.QPolygonF([
                            QtCore.QPointF(cx - 0.5, cy - 0.5),
                            QtCore.QPointF(cx - 3.5, cy - 0.5),
                            QtCore.QPointF(cx - 0.5, cy - 3.5),
                        ])
                        tri2 = QtGui.QPolygonF([
                            QtCore.QPointF(cx + 0.5, cy + 0.5),
                            QtCore.QPointF(cx + 3.5, cy + 0.5),
                            QtCore.QPointF(cx + 0.5, cy + 3.5),
                        ])
                        p.drawPolygon(tri1)
                        p.drawPolygon(tri2)
                    else:
                        # 最大化态：macOS 经典对角双三角
                        tri1 = QtGui.QPolygonF([
                            QtCore.QPointF(cx - 2.9, cy + 2.9),
                            QtCore.QPointF(cx - 2.9, cy - 1.3),
                            QtCore.QPointF(cx + 1.3, cy + 2.9),
                        ])
                        tri2 = QtGui.QPolygonF([
                            QtCore.QPointF(cx + 2.9, cy - 2.9),
                            QtCore.QPointF(cx + 2.9, cy + 1.3),
                            QtCore.QPointF(cx - 1.3, cy - 2.9),
                        ])
                        p.drawPolygon(tri1)
                        p.drawPolygon(tri2)

        p.end()


class GlassTitleBar(W.QFrame):
    """一体化玻璃顶栏（高 38px）：左侧 macOS 三色按钮 + 左插槽 + 居中品牌/页面标题 + 右插槽。"""

    def __init__(self, brand: str = "SysDroidPro", parent=None):
        super().__init__(parent)
        repolish(self, titlebar=True)
        self.setFixedHeight(38)

        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 10, 0)
        lay.setSpacing(8)

        self.lights = MacTrafficLights(self)
        lay.addWidget(self.lights)

        sep = W.QFrame(self)
        repolish(sep, vline=True)
        sep.setFixedSize(1, 14)
        lay.addWidget(sep)

        self._left_lay = W.QHBoxLayout()
        self._left_lay.setContentsMargins(0, 0, 0, 0)
        self._left_lay.setSpacing(4)
        lay.addLayout(self._left_lay)

        lay.addStretch(1)

        center = W.QWidget(self)
        center.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        cl = W.QHBoxLayout(center)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(6)
        self._brand = W.QLabel(brand)
        self._brand.setFont(theme().ui_font(13, 800))
        repolish(self._brand, tbbrand=True)
        self._slash = W.QLabel("/")
        self._slash.setFont(theme().mono_font(12, 600))
        repolish(self._slash, role="faint")
        self._subtitle = W.QLabel("")
        self._subtitle.setFont(theme().ui_font(12, 700))
        repolish(self._subtitle, tbpage=True)
        self._slash.hide()
        self._subtitle.hide()
        cl.addWidget(self._brand)
        cl.addWidget(self._slash)
        cl.addWidget(self._subtitle)
        lay.addWidget(center)

        lay.addStretch(1)

        self._right_lay = W.QHBoxLayout()
        self._right_lay.setContentsMargins(0, 0, 0, 0)
        self._right_lay.setSpacing(4)
        lay.addLayout(self._right_lay)

    def add_left(self, w: W.QWidget):
        self._left_lay.addWidget(w)
        return w

    def add_right(self, w: W.QWidget):
        self._right_lay.addWidget(w)
        return w

    def set_subtitle(self, text: str):
        text = text.strip()
        self._subtitle.setText(text)
        self._slash.setVisible(bool(text))
        self._subtitle.setVisible(bool(text))

    def is_draggable(self, global_pos: QtCore.QPoint) -> bool:
        """全局逻辑坐标是否落在顶栏可拖拽空白区（避开三色按钮及交互控件）。"""
        local = self.mapFromGlobal(global_pos)
        if not self.rect().contains(local):
            return False
        w = self.childAt(local)
        while w is not None and w is not self:
            if isinstance(w, (MacTrafficLights, W.QAbstractButton, W.QLineEdit, W.QComboBox)):
                return False
            w = w.parentWidget()
        return True

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            gpos = e.globalPosition().toPoint() if hasattr(e, "globalPosition") else e.globalPos()
            if self.is_draggable(gpos):
                wh = self.window().windowHandle() if self.window() else None
                if wh is not None and wh.startSystemMove():
                    e.accept()
                    return
        super().mousePressEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            gpos = e.globalPosition().toPoint() if hasattr(e, "globalPosition") else e.globalPos()
            if self.is_draggable(gpos):
                win = self.window()
                if win is not None:
                    win.showNormal() if win.isMaximized() else win.showMaximized()
                    e.accept()
                    return
        super().mouseDoubleClickEvent(e)
