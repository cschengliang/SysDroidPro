"""设计系统组件的 Qt 实现。标准控件只做配置（样式在 style.qss），Qt 没有的控件在这里自绘。"""
from __future__ import annotations

from ._qt import QtCore, QtGui, QtWidgets, Qt, Signal, Property
from .theme import theme, repolish, bind_icon

W = QtWidgets
STATE_TEXT = {
    "device": ("在线", "ok"), "offline": ("离线", "danger"), "unauthorized": ("未授权", "warn"),
    "authorizing": ("授权中", "info"), "connecting": ("连接中", "info"),
    "recovery": ("Recovery", "special"), "fastboot": ("Fastboot", "special"), "sideload": ("Sideload", "special"),
}


def _no_mouse_focus(w):
    """按钮只在键盘 Tab 时获得焦点，焦点框不会在鼠标点击后残留。"""
    w.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    return w


# ── 操作 ──────────────────────────────────────────────

def button(text: str = "", variant: str = "secondary", size: str = "md", icon: str | None = None,
           menu: QtWidgets.QMenu | None = None, parent=None) -> QtWidgets.QPushButton:
    """Button：variant = primary / secondary / ghost / danger；size = sm / md / lg。"""
    b = _no_mouse_focus(W.QPushButton(text, parent))
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    repolish(b, variant=variant, size=size)
    if icon:
        color = {"primary": "on-signal", "danger": "on-danger"}.get(variant, "ink" if variant == "secondary" else "ink-muted")
        bind_icon(b, icon, color, 16, hover="on-signal" if variant == "primary" else ("on-danger" if variant == "danger" else "ink"))
    if menu is not None:
        b.setMenu(menu)
        repolish(b, menu=True)
    return b


def icon_button(icon: str, label: str, checkable: bool = False, tone: str | None = None,
                size: str = "sm", parent=None) -> QtWidgets.QToolButton:
    """IconButton：label 必填，作为悬停提示与无障碍名称。size sm=24 / md=28。"""
    b = _no_mouse_focus(W.QToolButton(parent))
    b.setAutoRaise(True)
    b.setToolTip(label)
    b.setAccessibleName(label)
    b.setCheckable(checkable)
    px = 24 if size == "sm" else 28
    b.setFixedSize(px, px)
    hover = "danger" if tone == "danger" else "ink"
    bind_icon(b, icon, "ink-muted", 16, hover=hover)
    if tone:
        repolish(b, tone=tone)
    return b


def menu(items: list, parent=None) -> QtWidgets.QMenu:
    """items: [(id, 文字, 图标或 None, 快捷键或 None, danger), ...]，None 为分隔线。返回的 QMenu 用 triggered 取 action.data()。"""
    m = W.QMenu(parent)
    t = theme()
    for it in items:
        if it is None:
            m.addSeparator()
            continue
        id_, text, icon, shortcut, danger = (list(it) + [None, None, False])[:5]
        a = m.addAction(text)
        a.setData(id_)
        if icon:
            a.setIcon(t.icon(icon, "danger" if danger else "ink-muted", 16))
        if shortcut:
            a.setShortcut(QtGui.QKeySequence(shortcut))
    return m


class Toolbar(W.QFrame):
    """工具页顶部 40px 操作区。add() 加控件，separator() 加分隔，stretch() 把后续控件推到右侧。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        repolish(self, toolbar=True)
        self.setFixedHeight(40)
        self._lay = W.QHBoxLayout(self)
        self._lay.setContentsMargins(8, 0, 8, 0)
        self._lay.setSpacing(4)

    def add(self, w):
        self._lay.addWidget(w)
        return w

    def separator(self):
        f = W.QFrame()
        repolish(f, vline=True)
        f.setFixedSize(1, 16)
        self._lay.addSpacing(4)
        self._lay.addWidget(f)
        self._lay.addSpacing(4)

    def stretch(self):
        self._lay.addStretch(1)


class SegmentedControl(W.QFrame):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str]], value: str, mono: bool = False, label: str = "", parent=None):
        super().__init__(parent)
        repolish(self, segmented=True)
        self.setSizePolicy(W.QSizePolicy.Policy.Fixed, W.QSizePolicy.Policy.Fixed)  # 放进工具栏时不被拉高
        self.setAccessibleName(label)
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(1)
        self._group = W.QButtonGroup(self)
        self._group.setExclusive(True)
        self._buttons = {}
        for val, text in options:
            b = W.QToolButton()
            b.setText(text)
            b.setCheckable(True)
            b.setChecked(val == value)
            b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
            repolish(b, segment=True)
            if mono:
                b.setFont(theme().mono_font(12, 600))
            self._group.addButton(b)
            self._buttons[val] = b
            lay.addWidget(b)
            b.toggled.connect(lambda on, v=val: on and self.changed.emit(v))

    def value(self) -> str:
        for v, b in self._buttons.items():
            if b.isChecked():
                return v
        return ""

    def set_value(self, value: str):
        """选中 value 对应的分段（会发出 changed）。"""
        if value in self._buttons:
            self._buttons[value].setChecked(True)


# ── 输入 ──────────────────────────────────────────────

class TextField(W.QWidget):
    """标签 + 输入框 + 帮助/错误文字。field.edit 是内部的 QLineEdit。"""

    def __init__(self, label: str = "", placeholder: str = "", hint: str = "", mono: bool = False, parent=None):
        super().__init__(parent)
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.label = W.QLabel(label)
        repolish(self.label, fieldlabel=True)
        self.edit = W.QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        if mono:
            self.edit.setFont(theme().mono_font(12))
        self._hint = hint
        self.hint = W.QLabel(hint)
        self.hint.setWordWrap(True)          # 长说明换行，不把所在页面撑出横向滚动条
        repolish(self.hint, fieldhint=True)
        self.label.setBuddy(self.edit)
        lay.addWidget(self.label)
        lay.addWidget(self.edit)
        lay.addWidget(self.hint)
        self.label.setVisible(bool(label))
        self.hint.setVisible(bool(hint))

    def set_error(self, message: str | None):
        repolish(self.edit, invalid=bool(message))
        self.hint.setText(message or self._hint)
        repolish(self.hint, fieldhint="error" if message else True)
        self.hint.setVisible(bool(message or self._hint))


class FilterField(W.QFrame):
    """Logcat 过滤框：搜索图标、等宽输入、命中计数、大小写/全字/正则开关。"""

    textChanged = Signal(str)
    optionsChanged = Signal(dict)

    def __init__(self, placeholder: str = "过滤：tag、包名或关键字", width: int = 320, parent=None):
        super().__init__(parent)
        repolish(self, filter=True)
        self.setFixedWidth(width)
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 2, 0)
        lay.setSpacing(4)
        self._icon = W.QLabel()
        lay.addWidget(self._icon)
        self.edit = W.QLineEdit()
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFont(theme().mono_font(12))
        self.edit.installEventFilter(self)
        self.edit.textChanged.connect(self.textChanged)
        lay.addWidget(self.edit, 1)
        self.count = W.QLabel()
        self.count.setFont(theme().mono_font(11))
        repolish(self.count, filtercount=True)
        self.count.hide()
        lay.addWidget(self.count)
        self._toggles = {}
        for key, icon, tip in (("matchCase", "case-sensitive", "区分大小写"), ("wholeWord", "whole-word", "全字匹配"), ("regex", "regex", "正则表达式")):
            b = W.QToolButton()
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setAccessibleName(tip)
            b.setFixedSize(20, 18)
            b.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            repolish(b, filtertoggle=True)
            bind_icon(b, icon, "ink-faint", 14)
            b.toggled.connect(lambda *_: self.optionsChanged.emit(self.options()))
            self._toggles[key] = b
            lay.addWidget(b)
        self._paint_icon()
        theme().changed.connect(self._paint_icon)

    def _paint_icon(self, *_):
        self._icon.setPixmap(theme().pixmap("search", "ink-faint", 16))

    def eventFilter(self, obj, ev):
        if obj is self.edit and ev.type() in (QtCore.QEvent.Type.FocusIn, QtCore.QEvent.Type.FocusOut):
            repolish(self, focused=ev.type() == QtCore.QEvent.Type.FocusIn)
        return super().eventFilter(obj, ev)

    def options(self) -> dict:
        return {k: b.isChecked() for k, b in self._toggles.items()}

    def text(self) -> str:
        return self.edit.text()

    def set_count(self, matches: int | None, total: int | None = None):
        if matches is None or not self.edit.text():
            self.count.hide()
            return
        self.count.setText(f"{matches:,}" + (f" / {total:,}" if total is not None else ""))
        repolish(self.count, filtercount="none" if matches == 0 else True)
        self.count.show()

    def set_invalid(self, invalid: bool):
        repolish(self, invalid=invalid)


def select(options: list[tuple[str, str]], value: str | None = None, size: str = "md", mono: bool = False) -> QtWidgets.QComboBox:
    """Select：options 为 [(值, 文字)]，用 currentData() 取值。"""
    c = W.QComboBox()
    for v, t in options:
        c.addItem(t, v)
    if value is not None:
        c.setCurrentIndex(max(0, c.findData(value)))
    if mono:
        c.setFont(theme().mono_font(12))
    repolish(c, size=size)
    c.setFocusPolicy(Qt.FocusPolicy.TabFocus)
    return c


class Switch(W.QAbstractButton):
    """开关：32×18 轨道，10px 圆点，切换动画 120ms。文字放在右侧。"""

    def __init__(self, text: str = "", checked: bool = False, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self._pos = 1.0 if checked else 0.0
        self._anim = QtCore.QPropertyAnimation(self, b"knob", self)
        self._anim.setDuration(120)
        self._anim.setEasingCurve(QtCore.QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)
        theme().changed.connect(self._on_theme)

    def _on_theme(self, *_):
        """主题切换后重绘。连接绑定方法而不是 lambda：控件销毁时 Qt 会自动断开。"""
        self.update()

    def _get_pos(self):
        return self._pos

    def _set_pos(self, v):
        self._pos = v
        self.update()

    knob = Property(float, _get_pos, _set_pos)

    def _animate(self, on):
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if on else 0.0)
        self._anim.start()

    def sizeHint(self):
        fm = self.fontMetrics()
        return QtCore.QSize(32 + (8 + fm.horizontalAdvance(self.text()) if self.text() else 0), 20)

    def minimumSizeHint(self):
        """放进工具栏时不被压到比文字还窄（否则文字被截断）。"""
        return self.sizeHint()

    def hitButton(self, pos):
        return self.rect().contains(pos)

    def paintEvent(self, _):
        t = theme()
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        track = QtCore.QRectF(0.5, (self.height() - 18) / 2 + 0.5, 31, 17)
        on = self.isChecked()
        enabled = self.isEnabled()
        if not enabled:
            fill, edge, knob = t.color("bg-300"), t.color("line"), t.color("ink-disabled")
        elif on:
            fill, edge, knob = t.color("signal"), t.color("signal-edge"), t.color("on-signal")
        else:
            fill, edge = t.color("bg-300"), t.color("line-strong")
            knob = t.color("ink") if self.underMouse() else t.color("ink-muted")
        p.setPen(QtGui.QPen(edge, 1))
        p.setBrush(fill)
        p.drawRoundedRect(track, 8.5, 8.5)
        x = track.left() + 3.5 + 14 * self._pos
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(knob)
        p.drawEllipse(QtCore.QRectF(x, track.top() + 3.5, 10, 10))
        if self.hasFocus():
            p.setPen(QtGui.QPen(t.color("focus"), 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(track.adjusted(-2, -2, 2, 2), 10, 10)
        if self.text():
            p.setPen(t.color("ink") if enabled else t.color("ink-disabled"))
            p.setFont(self.font())
            p.drawText(QtCore.QRectF(40, 0, self.width() - 40, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


# ── 设备 ──────────────────────────────────────────────

class DeviceStatus(W.QWidget):
    """状态圆点 + 中文状态词；连接中/授权中圆点闪烁。compact=True 只画圆点。"""

    def __init__(self, state: str = "device", compact: bool = False, parent=None):
        super().__init__(parent)
        self._compact = compact
        self._blink = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(600)
        self._timer.timeout.connect(self._tick)
        self.setFont(theme().ui_font(12))
        theme().changed.connect(self._on_theme)
        self.set_state(state)

    def _on_theme(self, *_):
        """主题切换后重绘。连接绑定方法而不是 lambda：控件销毁时 Qt 会自动断开。"""
        self.update()

    def set_state(self, state: str):
        self._state = state
        text, self._tone = STATE_TEXT.get(state, STATE_TEXT["offline"])
        self._text = text
        self.setToolTip(text if self._compact else "")
        self.setAccessibleName(text)
        if state in ("connecting", "authorizing"):
            self._timer.start()
        else:
            self._timer.stop()
            self._blink = False
        self.updateGeometry()
        self.update()

    def _tick(self):
        self._blink = not self._blink
        self.update()

    def sizeHint(self):
        if self._compact:
            return QtCore.QSize(8, 16)
        return QtCore.QSize(14 + self.fontMetrics().horizontalAdvance(self._text), 16)

    def paintEvent(self, _):
        c = theme().color(self._tone)
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        dot = QtGui.QColor(c)
        if self._blink:
            dot.setAlphaF(0.35)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(dot)
        y = (self.height() - 8) / 2
        p.drawEllipse(QtCore.QRectF(0, y, 8, 8))
        if not self._compact:
            p.setPen(c)
            p.drawText(QtCore.QRectF(14, 0, self.width() - 14, self.height()),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self._text)
        p.end()


# ── 反馈 ──────────────────────────────────────────────

def badge(text: str, tone: str = "neutral", mono: bool = False) -> QtWidgets.QLabel:
    """Badge：tone = neutral / signal / ok / warn / danger / info / special。"""
    l = W.QLabel(text)
    if mono:
        l.setFont(theme().mono_font(11, 500))
    repolish(l, badge=tone)
    l.setSizePolicy(W.QSizePolicy.Policy.Fixed, W.QSizePolicy.Policy.Fixed)
    return l


class Banner(W.QFrame):
    """提示条：tone = info / ok / warn / danger。标题说发生了什么，正文说怎么办。"""

    dismissed = Signal()
    ICONS = {"info": "info", "ok": "circle-check", "warn": "triangle-alert", "danger": "circle-alert"}

    def __init__(self, tone: str, title: str, body: str = "", action: QtWidgets.QWidget | None = None,
                 dismissible: bool = False, parent=None):
        super().__init__(parent)
        repolish(self, banner=tone)
        self._tone = tone
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(16, 12, 12, 12)
        lay.setSpacing(12)
        self._icon = W.QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        lay.addWidget(self._icon, 0, Qt.AlignmentFlag.AlignTop)
        col = W.QVBoxLayout()
        col.setSpacing(0)
        t = W.QLabel(title)
        repolish(t, bannertitle=True)
        col.addWidget(t)
        if body:
            b = W.QLabel(body)
            b.setWordWrap(True)
            col.addWidget(b)
        lay.addLayout(col, 1)
        if action is not None:
            lay.addWidget(action, 0, Qt.AlignmentFlag.AlignVCenter)
        if dismissible:
            x = icon_button("x", "关闭提示")
            x.clicked.connect(self.dismissed)
            x.clicked.connect(self.hide)
            lay.addWidget(x, 0, Qt.AlignmentFlag.AlignTop)
        self._paint_icon()
        theme().changed.connect(self._paint_icon)

    def _paint_icon(self, *_):
        self._icon.setPixmap(theme().pixmap(self.ICONS[self._tone], self._tone, 16))


class ProgressRow(W.QWidget):
    """进度：标签 + 百分比、4px 进度条、等宽明细。value=None 为不确定进度。"""

    def __init__(self, label: str = "", value: int | None = 0, detail: str = "", parent=None):
        super().__init__(parent)
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        top = W.QHBoxLayout()
        self.label = W.QLabel(label)
        self.pct = W.QLabel()
        self.pct.setFont(theme().mono_font(12))
        repolish(self.pct, progresspct=True)
        top.addWidget(self.label, 1)
        top.addWidget(self.pct)
        lay.addLayout(top)
        self.bar = W.QProgressBar()
        self.bar.setTextVisible(False)
        lay.addWidget(self.bar)
        self.detail = W.QLabel(detail)
        self.detail.setFont(theme().mono_font(11))
        repolish(self.detail, progressdetail=True)
        self.detail.setVisible(bool(detail))
        lay.addWidget(self.detail)
        self.set_value(value)

    def set_value(self, value: int | None, detail: str | None = None, failed: bool = False):
        if value is None:
            self.bar.setRange(0, 0)
            self.pct.hide()
        else:
            self.bar.setRange(0, 100)
            self.bar.setValue(int(value))
            self.pct.setText(f"{int(value)}%")
            self.pct.show()
        repolish(self.bar, tone="danger" if failed else "signal")
        if detail is not None:
            self.detail.setText(detail)
            self.detail.setVisible(bool(detail))


class EmptyState(W.QWidget):
    """空状态：图标、24px 标题、说明或步骤、操作按钮。"""

    def __init__(self, icon: str, title: str, body: str = "", actions: list | None = None, parent=None):
        super().__init__(parent)
        outer = W.QVBoxLayout(self)
        outer.setContentsMargins(24, 48, 24, 24)
        outer.addStretch(1)
        box = W.QFrame()
        repolish(box, emptyicon=True)
        box.setFixedSize(64, 64)
        bl = W.QVBoxLayout(box)
        self._icon = W.QLabel()
        self._icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bl.addWidget(self._icon)
        self._icon_name = icon
        outer.addWidget(box, 0, Qt.AlignmentFlag.AlignHCenter)
        outer.addSpacing(16)
        t = W.QLabel(title)
        t.setFont(theme().display_font(24))
        outer.addWidget(t, 0, Qt.AlignmentFlag.AlignHCenter)
        if body:
            b = W.QLabel(body)
            b.setWordWrap(True)
            b.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            b.setMaximumWidth(440)
            # 换行标签不能带对齐参数加入布局：那样按 sizeHint 的宽度估算高度，实际更窄时末行被截掉。
            # 放进两侧留弹性的横排里，标签占满（≤440）宽度，高度按实际宽度计算。
            b.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Preferred)
            repolish(b, role="muted")
            outer.addSpacing(8)
            row = W.QHBoxLayout()
            row.addStretch(1)
            row.addWidget(b, 100)          # 宽度优先给标签（上限 440），剩余留给两侧
            row.addStretch(1)
            outer.addLayout(row)
        if actions:
            outer.addSpacing(24)
            row = W.QHBoxLayout()
            row.setSpacing(8)
            row.addStretch(1)
            for a in actions:
                row.addWidget(a)
            row.addStretch(1)
            outer.addLayout(row)
        outer.addStretch(2)
        self._paint_icon()
        theme().changed.connect(self._paint_icon)

    def _paint_icon(self, *_):
        self._icon.setPixmap(theme().pixmap(self._icon_name, "ink-muted", 32))


def confirm(parent, title: str, body: str, confirm_text: str, danger: bool = False, cancel_text: str = "取消") -> bool:
    """确认对话框。危险操作时主按钮为 danger，初始焦点在“取消”。"""
    d = W.QDialog(parent)
    d.setWindowTitle(title)
    repolish(d, tb=True)
    d.setFixedWidth(440)
    lay = W.QVBoxLayout(d)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    body_box = W.QWidget()
    bl = W.QVBoxLayout(body_box)
    bl.setContentsMargins(20, 20, 20, 20)
    bl.setSpacing(12)
    head = W.QHBoxLayout()
    head.setSpacing(8)
    if danger:
        ic = W.QLabel()
        ic.setPixmap(theme().pixmap("triangle-alert", "danger", 20))
        head.addWidget(ic)
    tl = W.QLabel(title)
    tl.setFont(theme().display_font(18))
    head.addWidget(tl, 1)
    bl.addLayout(head)
    b = W.QLabel(body)
    b.setWordWrap(True)
    b.setTextFormat(Qt.TextFormat.RichText)
    repolish(b, dialogbody=True)
    bl.addWidget(b)
    lay.addWidget(body_box)
    foot = W.QFrame()
    repolish(foot, dialogfooter=True)
    fl = W.QHBoxLayout(foot)
    fl.setContentsMargins(20, 16, 20, 16)
    fl.setSpacing(8)
    fl.addStretch(1)
    cancel = button(cancel_text, size="lg")
    ok = button(confirm_text, "danger" if danger else "primary", "lg")
    for x in (cancel, ok):
        x.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        fl.addWidget(x)
    cancel.clicked.connect(d.reject)
    ok.clicked.connect(d.accept)
    lay.addWidget(foot)
    (cancel if danger else ok).setDefault(True)
    (cancel if danger else ok).setFocus()
    return d.exec() == W.QDialog.DialogCode.Accepted


def kbd(keys: list[str]) -> QtWidgets.QWidget:
    """快捷键键帽：kbd(["Ctrl", "Shift", "S"])。"""
    w = W.QWidget()
    lay = W.QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(2)
    for i, k in enumerate(keys):
        if i:
            plus = W.QLabel("+")
            repolish(plus, role="faint")
            lay.addWidget(plus)
        l = W.QLabel(k)
        l.setFont(theme().mono_font(11))
        repolish(l, kbd=True)
        lay.addWidget(l)
    return w


def card(title: str, content: QtWidgets.QWidget) -> QtWidgets.QFrame:
    """带标题的卡片：bg-100 底、line 描边、6px 圆角。"""
    f = W.QFrame()
    repolish(f, card=True)
    lay = W.QVBoxLayout(f)
    lay.setContentsMargins(1, 1, 1, 1)
    lay.setSpacing(0)
    t = W.QLabel(title)
    repolish(t, cardtitle=True)
    lay.addWidget(t)
    lay.addWidget(content)
    return f


class PropertyList(W.QTableWidget):
    """键值列表：值用等宽字体，双击或 Ctrl+C 复制值。"""

    def __init__(self, items: list[tuple[str, str]], mono_keys: bool = False, parent=None):
        super().__init__(len(items), 2, parent)
        t = theme()
        self.horizontalHeader().hide()
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setEditTriggers(W.QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionBehavior(W.QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(W.QAbstractItemView.SelectionMode.SingleSelection)
        self.verticalHeader().setDefaultSectionSize(28)
        self.verticalHeader().setSectionResizeMode(W.QHeaderView.ResizeMode.Fixed)
        self.horizontalHeader().setStretchLastSection(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight)
        for r, (k, v) in enumerate(items):
            ki = W.QTableWidgetItem(k)
            ki.setForeground(t.color("ink-muted"))
            if mono_keys:
                ki.setFont(t.mono_font(12))
            vi = W.QTableWidgetItem(v)
            vi.setFont(t.mono_font(13, 500))
            vi.setToolTip(v)
            self.setItem(r, 0, ki)
            self.setItem(r, 1, vi)
        self.setFixedHeight(28 * len(items))
        self.itemDoubleClicked.connect(self._copy)
        sc = QtGui.QShortcut(QtGui.QKeySequence.StandardKey.Copy, self)
        sc.activated.connect(lambda: self.currentItem() and self._copy(self.currentItem()))
        theme().changed.connect(self._recolor)

    def _recolor(self, *_):
        for r in range(self.rowCount()):
            self.item(r, 0).setForeground(theme().color("ink-muted"))

    def resizeEvent(self, e):
        # 键名列：35%，限定在 110–170px（宽时不浪费、窄时键名不被截断，值列拿到更多空间）
        self.setColumnWidth(0, max(110, min(170, int(self.viewport().width() * 0.35))))
        super().resizeEvent(e)

    def _copy(self, item):
        QtWidgets.QApplication.clipboard().setText(self.item(item.row(), 1).text())


TOKEN_PROPERTY = QtGui.QTextFormat.Property.UserProperty.value + 1   # 字符格式里记下颜色令牌名


def token_format(token: str, bold: bool = False) -> QtGui.QTextCharFormat:
    """按颜色令牌着色的字符格式；令牌名记在格式里，主题切换后 retint() 据此重新着色。"""
    f = QtGui.QTextCharFormat()
    f.setForeground(theme().color(token))
    f.setProperty(TOKEN_PROPERTY, token)
    if bold:
        f.setFontWeight(QtGui.QFont.Weight.DemiBold)
    return f


def retint(doc: QtGui.QTextDocument):
    """把 token_format 写入的文字按当前主题重新着色（插入时的颜色是固定值，不会跟着主题变）。"""
    t = theme()
    c = QtGui.QTextCursor(doc)
    c.beginEditBlock()
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            token = frag.charFormat().property(TOKEN_PROPERTY) if frag.isValid() else None
            if token:
                c.setPosition(frag.position())
                c.setPosition(frag.position() + frag.length(), QtGui.QTextCursor.MoveMode.KeepAnchor)
                f = QtGui.QTextCharFormat()
                f.setForeground(t.color(token))
                c.mergeCharFormat(f)
            it += 1
        block = block.next()
    c.endEditBlock()


class Terminal(W.QWidget):
    """adb shell 终端：只读输出 + 提示符输入行，↑/↓ 浏览历史。submitted 信号发出命令文本。"""

    submitted = Signal(str)

    def __init__(self, prompt: str = "shell@device:/ $", parent=None):
        super().__init__(parent)
        t = theme()
        self.prompt = prompt
        self._hist: list[str] = []
        self._hi = -1
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.out = W.QPlainTextEdit()
        self.out.setReadOnly(True)
        self.out.setMaximumBlockCount(20000)
        self.out.setFont(t.mono_font(12))
        repolish(self.out, terminal=True)
        lay.addWidget(self.out, 1)
        row = W.QFrame()
        repolish(row, terminalinput=True)
        rl = W.QHBoxLayout(row)
        rl.setContentsMargins(12, 4, 12, 8)
        rl.setSpacing(8)
        self._prompt = W.QLabel(prompt)
        self._prompt.setFont(t.mono_font(12, 600))
        rl.addWidget(self._prompt)
        self.input = W.QLineEdit()
        self.input.setFont(t.mono_font(12))
        self.input.setAccessibleName("Shell 命令")
        self.input.returnPressed.connect(self._submit)
        self.input.installEventFilter(self)
        rl.addWidget(self.input, 1)
        lay.addWidget(row)
        theme().changed.connect(self._on_theme)

    def _on_theme(self, *_):
        """已输出的文字按新主题重新着色，否则切到深色后浅色主题下写的深色字看不清。绑定方法：控件销毁时自动断开。"""
        retint(self.out.document())

    def _fmt(self, token: str, bold: bool = False):
        return token_format(token, bold)

    def write(self, text: str, kind: str = "output"):
        """kind = input / output / error / system。"""
        c = self.out.textCursor()
        c.movePosition(QtGui.QTextCursor.MoveOperation.End)
        if not self.out.document().isEmpty():
            c.insertBlock()
        if kind == "input":
            c.insertText(self.prompt + " ", self._fmt("signal-ink", True))
            c.insertText(text, self._fmt("ink"))
        else:
            c.insertText(text, self._fmt({"error": "danger", "system": "ink-faint"}.get(kind, "ink")))
        self.out.setTextCursor(c)
        self.out.ensureCursorVisible()

    def _submit(self):
        cmd = self.input.text()
        if not cmd.strip():
            return
        self.write(cmd, "input")
        self._hist.append(cmd)
        self._hi = -1
        self.input.clear()
        self.submitted.emit(cmd)

    def eventFilter(self, obj, ev):
        if obj is self.input and ev.type() == QtCore.QEvent.Type.KeyPress and self._hist:
            if ev.key() == Qt.Key.Key_Up:
                self._hi = len(self._hist) - 1 if self._hi < 0 else max(0, self._hi - 1)
                self.input.setText(self._hist[self._hi])
                return True
            if ev.key() == Qt.Key.Key_Down and self._hi >= 0:
                self._hi += 1
                if self._hi >= len(self._hist):
                    self._hi = -1
                    self.input.clear()
                else:
                    self.input.setText(self._hist[self._hi])
                return True
        return super().eventFilter(obj, ev)
