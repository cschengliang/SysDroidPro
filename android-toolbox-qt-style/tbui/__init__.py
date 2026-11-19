"""Android 调试工具箱 — Qt6 界面样式包（PySide6 / PyQt6）。

    from tbui import install
    theme = install(app, "dark")
"""
from ._qt import BINDING
from .theme import Theme, theme, install, repolish, bind_icon, qcolor
from .backdrop import (apply_backdrop, apply_titlebar_theme, bind_backdrop,
                       enable_frameless_frame, handle_frameless_native_event, supports_glass)
from .titlebar import GlassTitleBar, MacTrafficLights
from .widgets import (button, icon_button, menu, Toolbar, SegmentedControl, TextField, FilterField, select,
                      Switch, DeviceStatus, badge, Banner, ProgressRow, EmptyState, confirm, kbd, card, GlassCard,
                      PropertyList, Terminal)
from .lists import Sidebar, DeviceList
from .logview import LogRow, LogModel, LogFilterProxy, LogDelegate, LogView

__all__ = [n for n in dir() if not n.startswith("_")]
