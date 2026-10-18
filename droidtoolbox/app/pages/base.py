"""工具页基类：按设备状态切换“内容 / 空状态”，并在合适的时机调用 load / unload。

生命周期：
    build()        构造时调用一次，在 self.body 里搭界面
    load(device)   页面可见、设备满足 requires，且设备（serial 或 state）与上次不同时调用
    unload()       设备断开 / 切换 / 状态变化时调用，用来停止流、清空界面
    shown()        每次切到这一页
    hidden()       每次离开这一页
    refresh()      F5，默认重新 load
"""
from __future__ import annotations

import tbui as ui
from tbui._qt import QtWidgets

from ..backend.models import Device
from ..core.state import Context

W = QtWidgets


class Page(W.QWidget):
    id = ""
    title = ""
    icon = "layout-grid"
    #: 需要的设备状态："adb"（开机可执行 shell）、"fastboot"、"sideload"；空元组表示不需要设备
    requires: tuple[str, ...] = ("adb",)

    def __init__(self, ctx: Context):
        super().__init__()
        self.ctx = ctx
        self.device: Device | None = None
        self._loaded: tuple[str, str] | None = None
        self._visible = False
        self._stack = W.QStackedLayout(self)
        self._stack.setContentsMargins(0, 0, 0, 0)
        self.body = W.QWidget()
        self._stack.addWidget(self.body)
        self._empty: W.QWidget | None = None
        self.build()
        if self.requires:
            ctx.state.currentChanged.connect(self._on_current)

    # 子类实现 --------------------------------------------------------
    def build(self):
        pass

    def load(self, device: Device):
        pass

    def unload(self):
        pass

    def shown(self):
        pass

    def hidden(self):
        """切到别的页面时调用（流可以选择继续或暂停）。"""

    def refresh(self):
        if self.device is not None:
            self.load(self.device)

    def status_text(self) -> str:
        """状态栏右侧显示的页面信息。"""
        return ""

    # 基类逻辑 --------------------------------------------------------
    @property
    def serial(self) -> str:
        return self.device.serial if self.device else ""

    def accepts(self, d: Device | None) -> bool:
        if not self.requires:
            return True
        if d is None:
            return False
        return ("adb" in self.requires and d.is_adb) or ("fastboot" in self.requires and d.is_fastboot) or \
               ("sideload" in self.requires and d.state == "sideload")

    def set_visible_page(self, on: bool):
        was, self._visible = self._visible, on
        if on:
            self._sync()
            self.shown()
        elif was:
            self.hidden()

    def _on_current(self, d: Device | None):
        key = (d.serial, d.state) if d else None
        if self._loaded is not None and key != self._loaded:
            self._loaded = None
            self.device = None
            self.unload()
        if self._visible:
            self._sync()
            self.ctx.window.update_status(self)

    def _sync(self):
        if not self.requires:
            return
        d = self.ctx.state.current
        if self.accepts(d):
            self._stack.setCurrentWidget(self.body)
            key = (d.serial, d.state)
            if key != self._loaded:
                self._loaded = key
                self.device = d
                self.load(d)
        else:
            self._show_empty(d)

    def _show_empty(self, d: Device | None):
        if self._empty is not None:
            self._stack.removeWidget(self._empty)
            self._empty.deleteLater()
        self._empty = self._empty_for(d)
        self._stack.addWidget(self._empty)
        self._stack.setCurrentWidget(self._empty)

    def _empty_for(self, d: Device | None) -> W.QWidget:
        st = self.ctx.state
        go_devices = ui.button("Devices", icon="smartphone")
        go_devices.setToolTip("打开设备总览")
        go_devices.clicked.connect(lambda: self.ctx.open_page("devices"))
        if d is None:
            return ui.EmptyState("smartphone", "没有连接设备",
                                 "用 USB 连接手机，并在开发者选项中打开“USB 调试”；也可以在设备总览里通过 Wi-Fi 连接。",
                                 [go_devices])
        if d.state == "unauthorized":
            return ui.EmptyState("triangle-alert", f"{d.display_name} 未授权",
                                 "请解锁手机，在弹出的“允许 USB 调试吗？”对话框中勾选“一律允许”，然后点“允许”。"
                                 "没有弹框时重新插拔数据线，或在开发者选项中“撤消 USB 调试授权”后重试。")
        if d.state in ("offline", "connecting", "authorizing"):
            return ui.EmptyState("refresh-cw", f"{d.display_name} 正在连接…", "设备离线或正在重启，连接后这一页会自动加载。")

        def reboot(mode, text, tip, icon):
            b = ui.button(text, "primary", icon=icon)
            b.setToolTip(tip)
            b.clicked.connect(lambda: self.ctx.run(st.backend.reboot, d.serial, mode,
                                                   done=lambda _: self.ctx.notify("info", f"正在{tip}…", d.display_name)))
            return b

        if "adb" in self.requires and not d.is_adb:
            return ui.EmptyState("zap", f"设备处于 {d.state.capitalize()} 模式",
                                 f"“{self.title}”需要设备开机并连接 adb。",
                                 [reboot("system", "Reboot", "重启到系统", "rotate-ccw"), go_devices])
        if "fastboot" in self.requires and not d.is_fastboot:
            return ui.EmptyState("zap", "需要进入 Fastboot 模式",
                                 "刷写分区、切换槽位、解锁 Bootloader 都在 Fastboot（Bootloader）模式下进行。"
                                 "线刷包 sideload 需要设备处于 Recovery 的 “Apply update from ADB”。",
                                 [reboot("bootloader", "Bootloader", "重启到 Bootloader", "zap"),
                                  reboot("sideload", "Sideload", "重启到 Sideload", "download")])
        return ui.EmptyState("circle-alert", "当前设备不支持这一页", "")
