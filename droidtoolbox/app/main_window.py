"""主窗口：侧栏（设备切换器 + 工具导航）、页面栈、状态栏、浮动提示、全局快捷键。"""
from __future__ import annotations

import tbui as ui
from tbui._qt import QtCore, QtGui, QtWidgets, Qt

from .backend.models import Device, ToolInfo
from .core import jobs
from .core.state import AppState, Context
from .pages import GROUPS
from .pages.actions import connect_dialog
from .pages.base import Page
from .ui.common import ToastHost, hbox, label

W = QtWidgets


class DeviceSwitcher(W.QFrame):
    """侧栏顶部：当前设备的状态点、名称、序列号；点击弹出设备菜单。"""

    def __init__(self, state: AppState, window: "MainWindow"):
        super().__init__()
        self.state = state
        self.window = window
        ui.repolish(self, switcher=True)
        self.setFixedHeight(56)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("切换设备")
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(16, 0, 10, 0)
        lay.setSpacing(10)
        self.dot = ui.DeviceStatus("offline", compact=True)
        lay.addWidget(self.dot)
        col = W.QVBoxLayout()
        col.setSpacing(0)
        self.name = W.QLabel()
        self.name.setFont(ui.theme().ui_font(13, 600))
        self.serial = label("", "faint", mono=True, px=11)
        col.addWidget(self.name)
        col.addWidget(self.serial)
        self.text = W.QWidget()
        self.text.setLayout(col)
        lay.addWidget(self.text, 1)
        self.chev = W.QLabel()
        lay.addWidget(self.chev)
        self._paint_chev()
        ui.theme().changed.connect(self._paint_chev)
        state.currentChanged.connect(self.update_device)
        state.devicesChanged.connect(lambda *_: self.update_device(state.current))
        self.update_device(None)

    def _paint_chev(self, *_):
        self.chev.setPixmap(ui.theme().pixmap("chevron-down", "ink-muted", 16))

    def set_collapsed(self, on: bool):
        self.text.setVisible(not on)
        self.chev.setVisible(not on)
        self.layout().setContentsMargins(20 if on else 16, 0, 10, 0)

    def update_device(self, d: Device | None):
        if d is None:
            self.dot.set_state("offline")
            self.name.setText("No device")
            self.serial.setText(f"{len(self.state.devices)} available" if self.state.devices else "waiting…")
            return
        self.dot.set_state("fastboot" if d.state == "bootloader" else d.state)
        self.name.setText(d.display_name)
        self.serial.setText(d.serial)
        self.setToolTip(f"{d.display_name}\n{d.serial}\n点击切换设备")

    def mousePressEvent(self, e):
        m = W.QMenu(self)
        t = ui.theme()
        for d in self.state.devices:
            text = ui.widgets.STATE_TEXT.get("fastboot" if d.state == "bootloader" else d.state, ("?", ""))[0]
            a = m.addAction(t.icon("monitor-smartphone" if d.transport == "emulator" else "smartphone"),
                            f"{d.display_name}    {d.serial} · {text}")
            a.setData(d.serial)
            a.setCheckable(True)
            a.setChecked(d.serial == self.state.serial)
        if self.state.devices:
            m.addSeparator()
        a = m.addAction(t.icon("wifi"), "无线连接…")
        a.setData("__connect")
        a = m.addAction(t.icon("smartphone"), "打开设备总览")
        a.setData("__devices")
        chosen = m.exec(self.mapToGlobal(QtCore.QPoint(8, self.height())))
        if chosen is None:
            return
        if chosen.data() == "__connect":
            connect_dialog(self.window.pages["devices"])
        elif chosen.data() == "__devices":
            self.window.open_page("devices")
        else:
            self.state.select(chosen.data())


class MainWindow(W.QMainWindow):
    def __init__(self, state: AppState):
        super().__init__()
        self.state = state
        self.ctx = Context(state, self)
        self.setWindowTitle("Android 系统工程师工具箱")
        self.resize(1360, 860)
        self.setMinimumSize(1100, 680)

        central = W.QWidget()
        h = W.QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side = W.QWidget()
        side.setProperty("surface", "panel")
        sv = W.QVBoxLayout(side)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        self.switcher = DeviceSwitcher(state, self)
        sv.addWidget(self.switcher)
        self.side = ui.Sidebar()
        sv.addWidget(self.side, 1)
        foot = W.QFrame()
        ui.repolish(foot, sidefoot=True)
        fl = W.QHBoxLayout(foot)
        fl.setContentsMargins(8, 6, 8, 6)
        fl.setSpacing(4)
        self.collapse_btn = ui.icon_button("panel-left", "收起侧栏（Ctrl+B）")
        self.collapse_btn.clicked.connect(lambda: self.set_sidebar_collapsed(not self.side.collapsed))
        self.theme_btn = ui.icon_button("sun", "切换深浅色（Ctrl+T）")
        self.theme_btn.clicked.connect(ui.theme().toggle)
        fl.addWidget(self.collapse_btn)
        fl.addStretch(1)
        fl.addWidget(self.theme_btn)
        sv.addWidget(foot)
        h.addWidget(side)

        self.stack = W.QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)
        self.toasts = ToastHost(central)

        self.pages: dict[str, Page] = {}
        self._order: list[str] = []
        self._current: Page | None = None      # 页面构造时可能调用 update_status，需先定义
        for group, classes in GROUPS:
            self.side.add_group(group)
            for cls in classes:
                page = cls(self.ctx)
                self.pages[page.id] = page
                self._order.append(page.id)
                self.stack.addWidget(page)
                self.side.add_item(page.id, page.title, page.icon)
        self.side.currentChanged.connect(self._switch)

        self._build_status()
        self._shortcuts()

        state.notified.connect(self.toasts.show_toast)
        state.devicesChanged.connect(self._devices_changed)
        state.currentChanged.connect(lambda d: self._status_device(d))
        jobs.set_default_error_handler(state.report)
        ui.theme().changed.connect(lambda name: state.settings.set("theme", name))

        self.set_sidebar_collapsed(state.settings.get("sidebar_collapsed"))
        last = state.settings.get("last_page")
        self.open_page(last if last in self.pages else "devices")

    # 状态栏 ----------------------------------------------------------
    def _build_status(self):
        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        self.adb_dot = ui.DeviceStatus("offline", compact=True)
        self.adb_label = W.QLabel("adb …")
        sb.addWidget(hbox(self.adb_dot, self.adb_label, spacing=4, margins=(8, 0, 0, 0)))
        if self.state.backend.name != "adb":
            mock = ui.badge("MOCK", "warn", mono=True)
            mock.setToolTip("没有找到 app/backend/adb.py，界面使用演示数据。实现 AdbBackend 后自动切换到真实设备。")
            sb.addWidget(mock)
        self.dev_label = W.QLabel()
        sb.addWidget(self.dev_label)
        self.page_label = W.QLabel()
        sb.addPermanentWidget(self.page_label)
        from . import __version__
        ver = W.QLabel(f"v{__version__}")
        ver.setFont(ui.theme().mono_font(11))
        sb.addPermanentWidget(ver)
        self.ctx.run(self.state.backend.tool_info, done=self.set_tool_info, error=lambda e: self.adb_label.setText(f"adb 不可用：{e}"))

    def set_tool_info(self, info: ToolInfo):
        if info.adb_path:
            self.adb_dot.set_state("device" if info.server_running else "unauthorized")
            self.adb_label.setText(f"adb {info.adb_version or ''}" + ("" if info.server_running else " · 服务未运行"))
            self.adb_label.setToolTip(info.adb_path)
        else:
            self.adb_dot.set_state("offline")
            self.adb_label.setText("adb not found")
            self.adb_label.setToolTip("未找到 adb，请在偏好设置中指定路径")

    def _status_device(self, d: Device | None):
        self.dev_label.setText(f"{d.display_name} · {d.serial}" if d else "")

    def _devices_changed(self, devices):
        online = sum(1 for d in devices if d.is_adb or d.is_fastboot)
        self.side.set_badge("devices", online or None)
        self.side.viewport().update()

    def update_status(self, page: Page):
        if page is self._current:
            self.page_label.setText(page.status_text() if page.device is not None or not page.requires else "")

    # 导航 ------------------------------------------------------------
    def open_page(self, page_id: str):
        if page_id in self.pages:
            self.side.select(page_id)

    def _switch(self, page_id: str):
        page = self.pages[page_id]
        if self._current is page:
            return
        if self._current is not None:
            self._current.set_visible_page(False)
        self._current = page
        self.stack.setCurrentWidget(page)
        page.set_visible_page(True)
        self.update_status(page)
        self.state.settings.set("last_page", page_id)

    def set_sidebar_collapsed(self, on: bool):
        self.side.set_collapsed(on)
        self.switcher.set_collapsed(on)
        self.switcher.setFixedWidth(48 if on else 200)
        self.theme_btn.setVisible(not on)
        self.collapse_btn.setToolTip("展开侧栏（Ctrl+B）" if on else "收起侧栏（Ctrl+B）")
        self.state.settings.set("sidebar_collapsed", on)

    def _shortcuts(self):
        def sc(key, fn):
            s = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            s.activated.connect(fn)
        sc("Ctrl+T", ui.theme().toggle)
        sc("Ctrl+B", lambda: self.set_sidebar_collapsed(not self.side.collapsed))
        sc("F5", lambda: self._current and self._current.refresh())
        for i, pid in enumerate(self._order[:9]):
            sc(f"Ctrl+{i + 1}", lambda pid=pid: self.open_page(pid))

    def closeEvent(self, e):
        for p in self.pages.values():
            if hasattr(p, "shutdown"):   # 功能启动页：停止本机服务进程
                p.shutdown()
        for p in self.pages.values():
            if p.device is not None:
                p.unload()
        super().closeEvent(e)
