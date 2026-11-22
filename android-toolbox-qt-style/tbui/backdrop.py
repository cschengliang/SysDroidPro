"""Win11 原生毛玻璃透底（Acrylic / Mica）与深色标题栏支持。

零外部依赖，仅用标准库 ctypes 调用 dwmapi.dll；非 Windows、Win10 或 offscreen 平台自动优雅降级。
"""
from __future__ import annotations

import ctypes
import sys

from ._qt import QtCore, QtGui, Qt
from .theme import theme, repolish

_DWMWA_USE_IMMERSIVE_DARK_MODE = 20
_DWMWA_USE_IMMERSIVE_DARK_MODE_OLD = 19
_DWMWA_WINDOW_CORNER_PREFERENCE = 33
_DWMWA_SYSTEMBACKDROP_TYPE = 38

_DWMWCP_ROUND = 2
_DWMSBT_NONE = 1
_DWMSBT_MAINWINDOW = 2       # Mica
_DWMSBT_TRANSIENTWINDOW = 3  # Acrylic（真·毛玻璃透底）

_WM_NCCALCSIZE = 0x0083
_WM_NCHITTEST = 0x0084

_GWL_STYLE = -16
_WS_SYSMENU = 0x00080000

_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOZORDER = 0x0004
_SWP_NOACTIVATE = 0x0010
_SWP_FRAMECHANGED = 0x0020

_HTCAPTION = 2
_HTLEFT = 10
_HTRIGHT = 11
_HTTOP = 12
_HTTOPLEFT = 13
_HTTOPRIGHT = 14
_HTBOTTOM = 15
_HTBOTTOMLEFT = 16
_HTBOTTOMRIGHT = 17

_SM_CXSIZEFRAME = 32
_SM_CXPADDEDBORDER = 92


class _MARGINS(ctypes.Structure):
    _fields_ = [
        ("cxLeftWidth", ctypes.c_int),
        ("cxRightWidth", ctypes.c_int),
        ("cyTopHeight", ctypes.c_int),
        ("cyBottomHeight", ctypes.c_int),
    ]


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt", _POINT),
    ]


class _NCCALCSIZE_PARAMS(ctypes.Structure):
    _fields_ = [
        ("rgrc", _RECT * 3),
        ("lppos", ctypes.c_void_p),
    ]


def supports_glass() -> bool:
    """仅真实 Windows 11 (22H2+, build >= 22621) 且非 offscreen 平台支持 DWM 系统毛玻璃。"""
    if sys.platform != "win32":
        return False
    app = QtGui.QGuiApplication.instance()
    if app is None or app.platformName() != "windows":
        return False
    try:
        return sys.getwindowsversion().build >= 22621
    except Exception:
        return False


def _hwnd(widget) -> int:
    try:
        return int(widget.winId())
    except Exception:
        return 0


def _dwm_set_int(hwnd: int, attr: int, val: int) -> bool:
    try:
        v = ctypes.c_int(val)
        hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attr, ctypes.byref(v), ctypes.sizeof(v))
        return hr == 0
    except Exception:
        return False


def apply_titlebar_theme(window, dark: bool) -> bool:
    """设置 Windows 原生标题栏明暗模式与 Win11 圆角。"""
    if sys.platform != "win32":
        return False
    app = QtGui.QGuiApplication.instance()
    if app is None or app.platformName() != "windows":
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    val = 1 if dark else 0
    ok = _dwm_set_int(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE, val)
    if not ok:
        ok = _dwm_set_int(hwnd, _DWMWA_USE_IMMERSIVE_DARK_MODE_OLD, val)
    if supports_glass():
        _dwm_set_int(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
    return ok


def apply_backdrop(window, theme_name: str, *, glass: bool = True) -> bool:
    """应用 Win11 Acrylic 毛玻璃背景与标题栏同步；返回当前窗口是否处于玻璃透底模式。"""
    apply_titlebar_theme(window, theme_name == "dark")
    if not supports_glass():
        if bool(window.property("glass")):
            repolish(window, glass=False)
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        repolish(window, glass=bool(glass))
        return bool(glass)
    if glass:
        try:
            margins = _MARGINS(-1, -1, -1, -1)
            ctypes.windll.dwmapi.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(margins))
        except Exception:
            pass
        applied = _dwm_set_int(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_TRANSIENTWINDOW)
        if not applied:
            applied = _dwm_set_int(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_MAINWINDOW)
        repolish(window, glass=applied)
        return applied
    _dwm_set_int(hwnd, _DWMWA_SYSTEMBACKDROP_TYPE, _DWMSBT_NONE)
    repolish(window, glass=False)
    return False


def enable_frameless_frame(window) -> bool:
    """隐藏原生标题栏按钮，保留 Win11 圆角、阴影和可缩放边框。"""
    if sys.platform != "win32":
        return False
    app = QtGui.QGuiApplication.instance()
    if app is None or app.platformName() != "windows":
        return False
    hwnd = _hwnd(window)
    if not hwnd:
        return False
    try:
        u32 = ctypes.windll.user32
        win_handle = ctypes.c_void_p(hwnd)
        style = u32.GetWindowLongW(win_handle, _GWL_STYLE)
        # WM_NCCALCSIZE 只扩大客户区，DWM 扩展玻璃框架时仍可能绘制原生按钮。
        # 关闭系统菜单及其标题栏按钮，保留 WS_CAPTION 以维持最大化工作区。
        if style & _WS_SYSMENU:
            u32.SetWindowLongW(win_handle, _GWL_STYLE, style & ~_WS_SYSMENU)
        if supports_glass():
            _dwm_set_int(hwnd, _DWMWA_WINDOW_CORNER_PREFERENCE, _DWMWCP_ROUND)
        flags = _SWP_FRAMECHANGED | _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOZORDER | _SWP_NOACTIVATE
        return bool(u32.SetWindowPos(win_handle, None, 0, 0, 0, 0, flags))
    except Exception:
        return False


def _resize_pad_px(hwnd: int) -> int:
    u32 = ctypes.windll.user32
    try:
        dpi = u32.GetDpiForWindow(hwnd) or 96
        return int(u32.GetSystemMetricsForDpi(_SM_CXSIZEFRAME, dpi)) + int(u32.GetSystemMetricsForDpi(_SM_CXPADDEDBORDER, dpi))
    except Exception:
        return int(u32.GetSystemMetrics(_SM_CXSIZEFRAME)) + int(u32.GetSystemMetrics(_SM_CXPADDEDBORDER))


def handle_frameless_native_event(window, event_type, message, titlebar, resize_margin: int = 6) -> tuple[bool, int]:
    """在 QMainWindow.nativeEvent 中处理无边框客户的 WM_NCCALCSIZE 与 WM_NCHITTEST。"""
    if sys.platform != "win32":
        return False, 0
    if event_type not in (b"windows_generic_MSG", "windows_generic_MSG"):
        return False, 0
    app = QtGui.QGuiApplication.instance()
    if app is None or app.platformName() != "windows":
        return False, 0
    try:
        msg = _MSG.from_address(int(message))
    except Exception:
        return False, 0
    hwnd = int(msg.hwnd or 0)
    if not hwnd:
        return False, 0

    if msg.message == _WM_NCCALCSIZE:
        if msg.wParam != 0 and ctypes.windll.user32.IsZoomed(hwnd):
            try:
                params = _NCCALCSIZE_PARAMS.from_address(int(msg.lParam))
                pad = _resize_pad_px(hwnd)
                params.rgrc[0].left += pad
                params.rgrc[0].top += pad
                params.rgrc[0].right -= pad
                params.rgrc[0].bottom -= pad
            except Exception:
                pass
        return True, 0

    if msg.message == _WM_NCHITTEST:
        u32 = ctypes.windll.user32
        x = ctypes.c_short(msg.lParam & 0xFFFF).value
        y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
        if not u32.IsZoomed(hwnd):
            rc = _RECT()
            if u32.GetWindowRect(hwnd, ctypes.byref(rc)):
                m = max(5, round(resize_margin * window.devicePixelRatioF()))
                on_l = rc.left <= x < rc.left + m
                on_r = rc.right - m <= x < rc.right
                on_t = rc.top <= y < rc.top + m
                on_b = rc.bottom - m <= y < rc.bottom
                if on_t and on_l:
                    return True, _HTTOPLEFT
                if on_t and on_r:
                    return True, _HTTOPRIGHT
                if on_b and on_l:
                    return True, _HTBOTTOMLEFT
                if on_b and on_r:
                    return True, _HTBOTTOMRIGHT
                if on_l:
                    return True, _HTLEFT
                if on_r:
                    return True, _HTRIGHT
                if on_t:
                    return True, _HTTOP
                if on_b:
                    return True, _HTBOTTOM
        if titlebar is not None and titlebar.is_draggable(QtGui.QCursor.pos()):
            return True, _HTCAPTION
    return False, 0


class _BackdropBinder(QtCore.QObject):
    def __init__(self, window, enabled_fn, frameless: bool = False):
        super().__init__(window)
        self.window = window
        self.enabled_fn = enabled_fn
        self.frameless = frameless
        self._frame_applied = False
        window.installEventFilter(self)
        theme().changed.connect(self.refresh)

    def is_enabled(self) -> bool:
        return True if self.enabled_fn is None else bool(self.enabled_fn())

    def sync_translucent_attr(self):
        want = supports_glass() and self.is_enabled()
        cur = self.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if cur != want:
            self.window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, want)

    def refresh(self, *_):
        try:
            want = self.is_enabled()
            if supports_glass() and not self.window.isVisible():
                self.sync_translucent_attr()
            if self.frameless and not self._frame_applied and self.window.isVisible():
                self._frame_applied = enable_frameless_frame(self.window)
            apply_backdrop(self.window, theme().name, glass=want)
            self.window.update()
        except RuntimeError:
            try:
                theme().changed.disconnect(self.refresh)
            except Exception:
                pass

    def eventFilter(self, obj, ev):
        if obj is self.window:
            if ev.type() == QtCore.QEvent.Type.WinIdChange:
                self._frame_applied = False
                self.refresh()
            elif ev.type() == QtCore.QEvent.Type.Show:
                self.refresh()
        return super().eventFilter(obj, ev)


def bind_backdrop(window, enabled_fn=None, *, frameless: bool = False):
    """在 show() 前绑定 Win11 毛玻璃透底与主题联动；frameless=True 时启用无边框客户区；返回 refresh 回调。"""
    binder = _BackdropBinder(window, enabled_fn, frameless=frameless)
    if supports_glass():
        window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        window.setProperty("glass", binder.is_enabled())
    else:
        window.setProperty("glass", False)
    window._tbui_backdrop_binder = binder
    return binder.refresh
