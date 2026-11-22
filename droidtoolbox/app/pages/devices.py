"""设备总览：左侧设备列表；右侧按“常用操作（Root/Remount）+ 系统与安全 + 硬件规格 + 刷机版本信息”分卡片呈现。

Remount 流程（userdebug）：未 root 先 adb root → adb remount → 输出提示需要重启时询问一次 →
重启并等待设备回来 → 自动再执行 root + remount。命令输出按需在操作卡底部展开记录。
"""
from __future__ import annotations

import time

import tbui as ui
from tbui._qt import QtWidgets, Qt, Signal

from ..backend.models import Device, DeviceDetail, RootState
from ..ui.common import PanelHead, TaskStrip, fmt_duration, fmt_size, hbox, hline, label, output_log, set_icon, tbtn, vline
from .actions import REBOOT_TEXT, connect_dialog, install_apks, pair_dialog
from .base import Page

W = QtWidgets
TRANSPORT_TEXT = {"usb": "USB", "wifi": "Wi-Fi", "emulator": "模拟器"}


class _Card(ui.GlassCard):
    """带标题栏操作按钮的光学玻璃卡片。"""

    def __init__(self, title: str, content: W.QWidget, *actions):
        super().__init__()
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.head = PanelHead(title, *actions)
        lay.addWidget(self.head)
        lay.addWidget(content, 1)


class _InfoRow(W.QFrame):
    """可点击复制的单行键值条目。"""

    clicked = Signal()

    def __init__(self, key: str, value: str, copy_value: str | None = None, tip: str | None = None,
                 badge_tone: str | None = None, parent=None):
        super().__init__(parent)
        self.key = key
        self.copy_value = value if copy_value is None else copy_value
        ui.repolish(self, inforow=True)
        self.setMinimumHeight(28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{tip}\n（点击复制）" if tip else f"{self.copy_value}（点击复制）")

        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(12)
        lay.addWidget(label(key, "muted"), 0)

        if badge_tone:
            lay.addStretch(1)
            lay.addWidget(ui.badge(value, badge_tone, mono=True), 0)
        else:
            val_lbl = label(value, mono=True)
            val_lbl.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Preferred)
            val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            lay.addWidget(val_lbl, 1)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class _MetricRow(W.QFrame):
    """可点击复制的硬件资源进度条目（内存 / 磁盘）。"""

    clicked = Signal()

    def __init__(self, title: str, summary: str, pct: int, copy_value: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.copy_value = copy_value
        ui.repolish(self, inforow=True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(f"{summary}（点击复制）")

        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 6)
        lay.setSpacing(5)
        top = W.QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.setSpacing(8)
        top.addWidget(label(title, "muted"), 0)
        val_lbl = label(summary, mono=True)
        val_lbl.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Preferred)
        val_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(val_lbl, 1)
        lay.addLayout(top)

        bar = W.QProgressBar()
        bar.setTextVisible(False)
        bar.setRange(0, 100)
        clamped = max(0, min(100, int(pct)))
        bar.setValue(clamped)
        ui.repolish(bar, tone="danger" if clamped >= 90 else "signal")
        lay.addWidget(bar)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton and self.rect().contains(e.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class DevicesPage(Page):
    id = "devices"
    title = "设备总览"
    icon = "smartphone"
    requires = ()

    def build(self):
        lay = W.QHBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        left = W.QWidget()
        left.setProperty("surface", "panel")
        left.setFixedWidth(252)
        ll = W.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        refresh = ui.icon_button("refresh-cw", "刷新设备列表和当前详情")
        refresh.clicked.connect(self.refresh)
        wifi_menu = ui.menu([("connect", "无线连接（adb connect）…", "link"), ("pair", "配对码配对（Android 11+）…", "wifi")], self)
        wifi_menu.triggered.connect(lambda a: connect_dialog(self) if a.data() == "connect" else pair_dialog(self))
        wifi = tbtn("Wi-Fi", "无线连接 / 配对", icon="wifi", menu=wifi_menu)
        self.head = PanelHead("设备", refresh, wifi)
        ll.addWidget(self.head)
        self.list = ui.DeviceList()
        ll.addWidget(self.list, 1)
        self.list_empty = label("未发现设备。\n\n连接数据线并打开 USB 调试，\n或点击“无线”通过网络连接。", "faint", wrap=True)
        self.list_empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.list_empty.setContentsMargins(16, 24, 16, 24)
        ll.addWidget(self.list_empty)
        self.inventory_status = label("", "caption", wrap=True)
        self.inventory_status.setContentsMargins(8, 4, 8, 4)
        ll.addWidget(self.inventory_status)
        ll.addWidget(hline())
        server = tbtn("Restart adb", "adb kill-server + start-server", "ghost", icon="rotate-ccw")
        server.clicked.connect(self._restart_server)
        ll.addWidget(hbox(server, None, margins=(8, 6, 8, 6)))
        lay.addWidget(left)
        lay.addWidget(vline())

        self._grid = None
        self.scroll = W.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        self.scroll.viewport().installEventFilter(self)
        lay.addWidget(self.scroll, 1)

        self.detail: DeviceDetail | None = None
        self.root: RootState | None = None
        self._detail_serial: str | None = None
        self._detail_generation = -1
        self._busy = False                         # Root / Remount 流程进行中
        self._pending_remount: tuple[str, str | None] | None = None
        self._remount_saw_disconnect = False
        self._has_output = False
        self._output_expanded = False
        self._last_output_summary = ""
        self._output_wrap = None
        self._flash_rows: list[tuple[str, str]] = []
        self._summary_text = ""

        self._log = output_log(120)
        self._log.out.textChanged.connect(self._on_log_text_changed)
        self._render(None)

        st = self.ctx.state
        st.devicesChanged.connect(self._devices)
        st.devicesScanned.connect(self._continue_remount)
        st.inventoryChanged.connect(self._inventory_status)
        st.currentChanged.connect(self._current)
        st.rootChanged.connect(lambda s: s == self._detail_serial and self._load_root())
        self.list.deviceSelected.connect(st.select)
        self.setAcceptDrops(True)
        self._devices(st.devices)
        self._inventory_status(st.inventory_sources)

    # 设备列表 --------------------------------------------------------
    def _devices(self, devices: list[Device]):
        self.head.set_count(len(devices) or None)
        self.list.blockSignals(True)
        self.list.set_devices([self._as_dict(d) for d in devices])
        cur = self.ctx.state.serial
        for i in range(self.list.count()):
            if self.list.item(i).data(ui.lists.ROLE_DEVICE)["serial"] == cur:
                self.list.setCurrentRow(i)
        self.list.blockSignals(False)
        self.list.setVisible(bool(devices))
        self.list_empty.setVisible(not devices)

    def _inventory_status(self, sources):
        text = {"pending": "刷新中…", "error": "刷新失败", "unavailable": "不可用"}
        notes = [f"{s.name}: {text[s.status]}" for s in sources if s.status in ("pending", "error")]
        if sources and all(s.status == "unavailable" for s in sources):
            notes.append("未找到 adb / fastboot")
        incomplete = any(s.status in ("pending", "error") for s in sources)
        self.list_empty.setText("暂未取得完整设备列表。" if incomplete else
                                "未发现设备。\n\n连接数据线并打开 USB 调试，\n或点击“无线”通过网络连接。")
        self.inventory_status.setText(" · ".join(notes))
        self.inventory_status.setToolTip("\n".join(f"{s.name}: {s.error}" for s in sources if s.error))
        self.inventory_status.setVisible(bool(notes))

    @staticmethod
    def _as_dict(d: Device) -> dict:
        state = "fastboot" if d.state == "bootloader" else d.state
        return dict(serial=d.serial, state=state, transport=d.transport, model=d.model or d.product or d.serial,
                    manufacturer=None if (d.manufacturer or "").lower() == "unknown" else d.manufacturer,
                    android=d.android, api=d.api, battery=d.battery)

    def _continue_remount(self, devices: list[Device]):
        if self._pending_remount is None:
            return
        if any(s.name == "adb" and s.status != "ready" for s in self.ctx.state.inventory_sources):
            return
        serial, connection_id = self._pending_remount
        device = next((d for d in devices if d.serial == serial and d.state == "device"), None)
        if device is None:
            self._remount_saw_disconnect = True
            return
        reconnected = device.connection_id is not None and device.connection_id != connection_id
        if self._busy or not (reconnected or self._remount_saw_disconnect):
            return
        self._pending_remount = None
        self._write_log(f"设备已重新连接（{time.strftime('%H:%M:%S')}），继续 root + remount…", "system", expand=True)
        self._remount_flow(serial, after_reboot=True)

    def _current(self, d: Device | None):
        if self._visible:
            self._render(d)

    def shown(self):
        d = self.ctx.state.current
        serial = d.serial if d else None
        if self._detail_generation != self.ctx.state.generation or self._detail_serial != serial:
            self._render(d)

    def refresh(self):
        self.ctx.state.refresh_devices()
        self._render(self.ctx.state.current)

    def eventFilter(self, obj, ev):
        if obj is self.scroll.viewport() and ev.type() == ev.Type.Resize:
            self._relayout()
        return False

    def _relayout(self):
        """系统与安全、硬件规格卡片：宽时并排，窄时上下堆叠，避免横向滚动。"""
        try:
            if self._grid is not None:
                narrow = self.scroll.viewport().width() < 820
                self._grid.setDirection(W.QBoxLayout.Direction.TopToBottom if narrow else W.QBoxLayout.Direction.LeftToRight)
        except RuntimeError:   # 详情已重绘，旧布局已销毁
            self._grid = None

    def _restart_server(self):
        self._write_log("adb kill-server && adb start-server", "input")
        self.ctx.run(
            self.ctx.backend.restart_server,
            done=lambda _: (
                self._write_log("adb 服务已重启", "output"),
                self.ctx.notify("ok", "adb 服务已重启"),
                self.ctx.state.refresh_devices(),
            ),
            error=lambda e: self._fail(e),
        )

    # 详情 ------------------------------------------------------------
    def _render(self, d: Device | None):
        self.detail = None
        self.root = None
        self._grid = None
        self._output_wrap = None
        self._requests.clear()
        self._flash_rows = []
        self._summary_text = ""
        self._detail_generation = self.ctx.state.generation
        self._detail_serial = d.serial if d else None

        page = W.QWidget(self.scroll.viewport())
        # 旧详情页会被滚动区销毁，提前迁移输出区，避免它成为独立窗口。
        self._log.setParent(page)
        self._log.hide()
        page.setProperty("page", True)
        v = W.QVBoxLayout(page)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)
        self._v = v

        if d is None:
            v.addWidget(ui.EmptyState("smartphone", "选择或连接一台设备",
                                      "左侧列出 adb 与 fastboot 识别到的全部设备。支持 USB、Wi-Fi 与模拟器。"))
            self.scroll.setWidget(page)
            return

        # 1. 顶部设备身份头
        name = W.QLabel(d.display_name)
        name.setToolTip(d.display_name)
        name.setMinimumWidth(0)
        name.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Preferred)
        name.setFont(ui.theme().display_font(22, 700))

        self.serial_btn = ui.button(d.serial, "ghost", "sm", icon="copy")
        self.serial_btn.setFont(ui.theme().mono_font(12, 600))
        self.serial_btn.setToolTip("点击复制设备序列号")
        self.serial_btn.clicked.connect(lambda _=False, s=d.serial: self._copy_value("序列号", s))

        transport_badge = ui.badge(TRANSPORT_TEXT.get(d.transport, d.transport), "neutral")
        self.bat_badge = ui.badge("", "neutral", mono=True)
        self.bat_badge.hide()

        state_widget = ui.DeviceStatus("fastboot" if d.state == "bootloader" else d.state)
        self._badges = hbox(state_widget, self.serial_btn, transport_badge, self.bat_badge, None, spacing=6)

        col = W.QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(name)
        col.addWidget(self._badges)

        right_actions = []
        self.copy_summary_btn = tbtn("复制摘要", "复制设备系统、硬件与刷机版本摘要", icon="copy")
        self.copy_summary_btn.clicked.connect(self._copy_summary)
        self.copy_summary_btn.hide()
        right_actions.append(self.copy_summary_btn)

        if d.transport == "wifi" and d.is_adb:
            self.disconnect_btn = tbtn("断开 Wi-Fi", "断开无线连接（adb disconnect）", "danger", icon="x")
            self.disconnect_btn.clicked.connect(lambda _=False, s=d.serial: self._disconnect_wifi(s))
            right_actions.append(self.disconnect_btn)
        else:
            self.disconnect_btn = None

        top = W.QHBoxLayout()
        top.setSpacing(12)
        top.addLayout(col, 1)
        top.addWidget(hbox(*right_actions, spacing=6), 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        # 2. TaskStrip 与条件状态横幅
        self.strip = TaskStrip(page=self)
        v.addWidget(self.strip)

        if d.state == "unauthorized":
            again = tbtn("重新检测", "重新检测设备连接状态", icon="refresh-cw")
            again.clicked.connect(self.ctx.state.refresh_devices)
            v.addWidget(ui.Banner("warn", f"{d.display_name} 未授权 USB 调试",
                                  "请在手机上勾选“一律允许使用这台计算机进行调试”，然后点“允许”。", action=again))
        elif d.state in ("offline", "connecting", "authorizing"):
            v.addWidget(ui.Banner("info", "设备正在连接", "设备重启或 adbd 重启后会自动刷新。"))
        elif not d.is_adb:
            reboot_sys = tbtn("重启到系统", "退出 Fastboot 回到 Android 系统", icon="rotate-ccw")
            reboot_sys.clicked.connect(lambda: self._reboot("system"))
            v.addWidget(ui.Banner("info", f"设备处于 {d.state.capitalize()} 模式",
                                  "可点击右侧“重启到系统”回到 Android，或使用“ADB 命令”页执行 fastboot 操作。", action=reboot_sys))

        # 3. 卡片 1：常用操作（只保留 Root / Remount + 按需输出抽屉）
        v.addWidget(self._control_card(d))

        # 4. 卡片 2/3/4 容器
        info = W.QWidget()
        self._info = W.QVBoxLayout(info)
        self._info.setContentsMargins(0, 0, 0, 0)
        self._info.setSpacing(12)
        v.addWidget(info)

        if d.is_adb:
            self._loading = ui.ProgressRow("正在读取系统、硬件与刷机版本信息…", None)
            self._info.addWidget(self._loading)
            self.request("details", self.ctx.backend.device_detail, d.serial,
                         done=self._got_detail, error=lambda e: self._loading.set_value(100, str(e), failed=True))
            self._load_root()
        else:
            empty_hint = label("当前设备尚未进入 ADB 在线状态；完成授权或重启进入系统后将自动加载信息卡片。", "faint", wrap=True)
            empty_hint.setContentsMargins(4, 8, 4, 8)
            self._info.addWidget(empty_hint)

        v.addStretch(1)
        self.scroll.setWidget(page)

    # 常用操作（只留 Root 与 Remount）---------------------------------
    def _control_card(self, d: Device) -> W.QWidget:
        box = W.QWidget()
        outer = W.QVBoxLayout(box)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        row = W.QWidget()
        h = W.QHBoxLayout(row)
        h.setContentsMargins(14, 10, 14, 10)
        h.setSpacing(8)

        self.root_btn = tbtn("Root", "adb root / unroot", "primary")
        set_icon(self.root_btn, "shield", "on-signal", hover="on-signal")
        self.root_state_lbl = ui.badge("adbd: —", "neutral", mono=True)

        sep = vline()
        sep.setFixedHeight(18)

        self.remount_btn = tbtn("Remount", "adb remount：未 root 时自动先 root；需要关闭 verity 时询问一次，重启后自动继续", "primary")
        set_icon(self.remount_btn, "hard-drive", "on-signal", hover="on-signal")
        self.rw_state_lbl = ui.badge("/system: — · verity: —", "neutral", mono=True)

        for w in (self.root_btn, self.root_state_lbl, 6, sep, 6, self.remount_btn, self.rw_state_lbl):
            h.addSpacing(w) if isinstance(w, int) else h.addWidget(w)
        h.addStretch(1)

        self.root_btn.clicked.connect(self._toggle_root)
        self.remount_btn.clicked.connect(lambda: self._remount_flow(self._detail_serial))

        adb = d.is_adb
        self.root_btn.setEnabled(adb)
        self.remount_btn.setEnabled(adb)
        outer.addWidget(row)

        # 按需滑出的命令输出底栏（无输出时隐藏）
        self._output_wrap = W.QFrame()
        ui.repolish(self._output_wrap, outputbar=True)
        ov = W.QVBoxLayout(self._output_wrap)
        ov.setContentsMargins(0, 0, 0, 0)
        ov.setSpacing(0)

        bar = W.QWidget()
        bh = W.QHBoxLayout(bar)
        bh.setContentsMargins(10, 4, 8, 4)
        bh.setSpacing(6)

        self._output_toggle_btn = ui.button("最近输出", "ghost", "sm")
        self._output_toggle_btn.clicked.connect(self._toggle_output)
        self._output_summary = label("", "muted", mono=True)
        self._output_summary.setMinimumWidth(0)
        self._output_summary.setSizePolicy(W.QSizePolicy.Policy.Ignored, W.QSizePolicy.Policy.Preferred)
        self._output_clear_btn = ui.icon_button("eraser", "清空并收起命令输出")
        self._output_clear_btn.clicked.connect(self._clear_output)

        bh.addWidget(self._output_toggle_btn, 0)
        bh.addWidget(self._output_summary, 1)
        bh.addWidget(self._output_clear_btn, 0)
        ov.addWidget(bar)
        ov.addWidget(self._log)
        outer.addWidget(self._output_wrap)

        self.control_hint = label(
            "Remount 未 root 时将自动先执行 adb root" if adb else "设备在线后可用",
            "faint",
            px=11,
        )
        self._sync_output_ui()
        return _Card("常用操作", box, self.control_hint)

    def _on_log_text_changed(self):
        plain = self._log.out.toPlainText().strip()
        if not plain:
            return
        last_line = next((ln.strip() for ln in reversed(plain.splitlines()) if ln.strip()), "")
        self._last_output_summary = last_line
        if not self._has_output:
            self._has_output = True
            self._output_expanded = True
        self._sync_output_ui()

    def _write_log(self, text: str, kind: str = "output", expand: bool = False):
        if expand:
            self._output_expanded = True
        self._has_output = True
        self._log.write(text, kind)
        self._sync_output_ui()

    def _toggle_output(self):
        self._output_expanded = not self._output_expanded
        self._sync_output_ui()

    def _clear_output(self):
        self._has_output = False
        self._output_expanded = False
        self._last_output_summary = ""
        self._log.out.clear()
        self._sync_output_ui()

    def _sync_output_ui(self):
        if self._output_wrap is None:
            return
        try:
            self._output_wrap.setVisible(self._has_output)
            self._output_summary.setText(self._last_output_summary)
            self._log.setVisible(self._has_output and self._output_expanded)
            set_icon(
                self._output_toggle_btn,
                "chevron-down" if self._output_expanded else "chevron-right",
                "ink-muted",
                14,
            )
            self._output_toggle_btn.setToolTip("收起命令输出" if self._output_expanded else "展开命令输出")
        except RuntimeError:
            pass

    def _disconnect_wifi(self, serial: str):
        self._write_log(f"adb disconnect {serial}", "input")
        self.ctx.run(
            self.ctx.backend.disconnect,
            serial,
            done=lambda _: (
                self._write_log(f"disconnected {serial}", "output"),
                self.ctx.notify("ok", "已断开无线连接", serial),
                self.ctx.state.refresh_devices(),
            ),
            error=lambda e: self._fail(e),
        )

    def _reboot(self, mode: str):
        d = self.ctx.state.current
        if d is None:
            return
        current = self.device_guard()
        if mode == "poweroff" and not ui.confirm(self, "关闭设备？", f"{d.display_name} 将关机，需要手动开机后才能再次连接。", "关机", danger=True):
            return
        if not current():
            return
        cmd = {"system": "adb reboot", "poweroff": "adb shell reboot -p"}.get(mode, f"adb reboot {mode}")
        real_cmd = cmd if d.is_adb else cmd.replace("adb", "fastboot")
        self._write_log(real_cmd, "input", expand=True)
        self.ctx.run(
            self.ctx.backend.reboot,
            d.serial,
            mode,
            done=lambda _: (
                self._write_log(f"{REBOOT_TEXT[mode]}…", "system"),
                self.ctx.notify("info", f"{REBOOT_TEXT[mode]}…", d.display_name),
            ),
            error=lambda e: self._fail(e),
        )

    def _load_root(self):
        serial = self._detail_serial
        if not serial or self._detail_generation != self.ctx.state.generation:
            return
        self.request("root", self.ctx.backend.root_state, serial, done=self._show_root, error=lambda _e: None)

    def _show_root(self, rs: RootState):
        self.root = rs
        try:
            user_build = rs.build_type == "user"
            self.root_state_lbl.setText("adbd: user build" if user_build else ("adbd: root" if rs.adbd_root else "adbd: shell"))
            ui.repolish(self.root_state_lbl, badge="ok" if (rs.adbd_root and not user_build) else "neutral")
            self.root_btn.setText("Unroot" if rs.adbd_root else "Root")
            self._variant(self.root_btn, "shield", primary=not rs.adbd_root)

            rw = "rw" if rs.system_writable else "ro" if rs.system_writable is False else "—"
            verity = "on" if rs.verity_enabled else "off" if rs.verity_enabled is False else "—"
            self.rw_state_lbl.setText(f"/system: {rw} · verity: {verity}")
            ui.repolish(self.rw_state_lbl, badge="ok" if rs.system_writable else "neutral")
            self._variant(self.remount_btn, "hard-drive", primary=not rs.system_writable)

            if user_build:
                self.control_hint.setText("user 固件不支持 adb root / remount")
                for b in (self.root_btn, self.remount_btn):
                    b.setEnabled(False)
                    b.setToolTip("user 版本固件不支持 adb root / remount")
            else:
                self.control_hint.setText("Remount 未 root 时将自动先执行 adb root")
                for b in (self.root_btn, self.remount_btn):
                    b.setEnabled(not self._busy)
        except RuntimeError:  # 页面已重绘
            pass

    @staticmethod
    def _variant(btn, icon: str, primary: bool):
        """切换按钮样式时同步图标颜色（primary 底色上用 on-signal，普通按钮用 ink）。"""
        ui.repolish(btn, variant="primary" if primary else "secondary")
        c = "on-signal" if primary else "ink"
        set_icon(btn, icon, c, hover=c)

    def _set_busy(self, on: bool):
        self._busy = on
        cur = self.ctx.state.current
        can_adb = bool(cur and cur.is_adb and (self.root is None or self.root.build_type != "user"))
        try:
            for b in (self.root_btn, self.remount_btn):
                b.setEnabled(not on and can_adb)
        except RuntimeError:
            pass

    def _fail(self, e):
        detail = getattr(e, "detail", "").strip()
        self._write_log(f"失败：{e}" + (f"\n{detail}" if detail else ""), "error", expand=True)
        self.ctx.report(e)

    def _toggle_root(self):
        serial = self._detail_serial
        if not serial or self._busy:
            return
        enable = not (self.root and self.root.adbd_root)
        self._set_busy(True)
        self._write_log("adb root" if enable else "adb unroot", "input", expand=True)

        def done(out):
            self._set_busy(False)
            self._write_log(out.strip() or "完成", "output", expand=True)
            self._load_root()

        def fail(e):
            self._set_busy(False)
            self._fail(e)

        self.ctx.run(self.ctx.backend.root, serial, enable, done=done, error=fail)

    def _remount_flow(self, serial: str | None, after_reboot: bool = False):
        if not serial or self._busy:
            return
        b = self.ctx.backend
        self._set_busy(True)

        def work(progress):
            """在工作线程中依次执行；progress((kind, text)) 把每一步写进输出区。"""
            rs = b.root_state(serial)
            if not rs.adbd_root:
                progress(("input", "adb root"))
                progress(("output", b.root(serial, True).strip()))
            progress(("input", "adb remount"))
            return b.remount(serial)

        def step(item):
            self._write_log(item[1], item[0], expand=True)

        def done(res):
            self._set_busy(False)
            self._write_log(res.output.strip(), "output", expand=True)
            self.ctx.state.rootChanged.emit(serial)
            if res.needs_reboot:
                if after_reboot:
                    self._write_log("重启后仍提示需要重启，请检查 remount 输出。", "error", expand=True)
                    return
                if ui.confirm(self, "需要重启后生效", "remount 已关闭 verity / 启用 overlayfs，需要重启设备后 /system 才可写。<br>"
                              "立即重启？设备启动后请刷新设备列表，检测到重连时会继续 root + remount。", "立即重启"):
                    self._set_busy(True)
                    self._write_log("adb reboot", "input", expand=True)

                    def reboot_done(device: Device):
                        # 先前执行的 adb root 也可能改变 transport ID。
                        self._pending_remount = (serial, device.connection_id)
                        self._remount_saw_disconnect = False
                        self._set_busy(False)
                        self._write_log("设备启动后请手动刷新列表。", "system", expand=True)

                    def reboot_failed(error):
                        self._set_busy(False)
                        self._fail(error)

                    self.ctx.run(b.reboot, serial, "system", done=reboot_done, error=reboot_failed)
                else:
                    self._write_log("已取消重启。稍后手动重启后再点 Remount。", "system", expand=True)
            else:
                self.ctx.notify("ok", "/system 已可写", "可以 adb push 修改后的模块了。")

        def fail(e):
            self._set_busy(False)
            self._fail(e)
            self._load_root()

        self.ctx.run(work, done=done, error=fail, progress=step)

    # 详情卡片（系统与安全 + 硬件规格 + 刷机版本信息）--------------------
    def _got_detail(self, dd: DeviceDetail):
        self.detail = dd
        self._loading.hide()
        self._info.removeWidget(self._loading)
        self._loading.deleteLater()

        def dash(x):
            return "—" if x in (None, "") else str(x)

        # 头部电量温度胶囊 + 复制摘要按钮
        if dd.battery and dd.battery.level is not None:
            lvl = dd.battery.level
            bat_text = f"电量 {lvl}% · {dd.battery.temperature_c:.1f}°C" if dd.battery.temperature_c is not None else f"电量 {lvl}%"
            bat_tone = "danger" if lvl <= 20 else ("warn" if lvl <= 45 else "neutral")
            self.bat_badge.setText(bat_text)
            ui.repolish(self.bat_badge, badge=bat_tone)
            bits = [x for x in (
                dd.battery.status,
                dd.battery.plugged,
                f"{dd.battery.voltage_v:.2f}V" if dd.battery.voltage_v is not None else None,
                f"{dd.battery.current_ma:+d}mA" if dd.battery.current_ma is not None else None,
            ) if x]
            self.bat_badge.setToolTip(" · ".join(bits) if bits else "电池状态")
            self.bat_badge.show()
        self.copy_summary_btn.show()

        # 卡片 2：系统与安全
        sys_box = W.QWidget()
        sl = W.QVBoxLayout(sys_box)
        sl.setContentsMargins(10, 8, 10, 10)
        sl.setSpacing(2)

        android_val = f"Android {dd.android} · API {dd.api}" if dd.android and dd.api else (f"Android {dd.android}" if dd.android else "—")
        slot_val = f"Slot {dd.slot}" if dd.slot else "—"
        se_val = dash(dd.selinux)
        se_tone = "warn" if (dd.selinux or "").lower() == "permissive" else "neutral"
        vb_val = dash(dd.verified_boot)
        vb_lower = (dd.verified_boot or "").lower()
        vb_tone = "ok" if vb_lower == "green" else ("warn" if vb_lower in ("orange", "yellow") else ("danger" if vb_lower == "red" else "neutral"))
        uptime_val = fmt_duration(dd.uptime_s)

        sys_rows = [
            ("Android 版本", android_val, None, None),
            ("内核版本", dash(dd.kernel), dd.kernel_full or None, None),
            ("当前槽位", slot_val, None, None),
            ("SELinux", se_val, None, se_tone if dd.selinux else None),
            ("Verified Boot", vb_val, None, vb_tone if dd.verified_boot else None),
            ("开机时长", uptime_val, None, None),
        ]
        for k, v, tip, btone in sys_rows:
            r = _InfoRow(k, v, tip=tip, badge_tone=btone)
            r.clicked.connect(lambda key=k, val=v: self._copy_value(key, val))
            sl.addWidget(r)
        sl.addStretch(1)
        sys_card = _Card("系统与安全", sys_box, label("点击条目复制", "faint", px=11))

        # 卡片 3：硬件规格（CPU + 屏幕 + 机型 + 内存 + 磁盘）
        hw_box = W.QWidget()
        hl = W.QVBoxLayout(hw_box)
        hl.setContentsMargins(10, 8, 10, 10)
        hl.setSpacing(2)

        screen_val = " · ".join(x for x in (dd.resolution, f"{dd.density} dpi" if dd.density else None) if x) or "—"
        maker = None if (dd.manufacturer or "").lower() == "unknown" else dd.manufacturer
        codename_val = " · ".join(x for x in (dd.device, maker) if x) or "—"
        hw_rows = [
            ("CPU / SoC", dash(dd.soc)),
            ("CPU 架构", dash(dd.abi)),
            ("屏幕参数", screen_val),
            ("机型代号", codename_val),
        ]
        for k, v in hw_rows:
            r = _InfoRow(k, v)
            r.clicked.connect(lambda key=k, val=v: self._copy_value(key, val))
            hl.addWidget(r)

        hl.addSpacing(4)
        hl.addWidget(hline())
        hl.addSpacing(4)

        mem_line = "—"
        if dd.mem_total and dd.mem_available is not None:
            used_mem = max(0, dd.mem_total - dd.mem_available)
            mem_pct = round(used_mem * 100 / dd.mem_total)
            mem_line = f"{fmt_size(used_mem)} / {fmt_size(dd.mem_total)} ({mem_pct}%)"
            mem_row = _MetricRow("内存 (RAM)", mem_line, mem_pct, mem_line)
            mem_row.clicked.connect(lambda val=mem_line: self._copy_value("内存信息", val))
            hl.addWidget(mem_row)
        else:
            if dd.mem_total:
                mem_line = f"总计 {fmt_size(dd.mem_total)}"
            mem_row = _InfoRow("内存 (RAM)", mem_line)
            mem_row.clicked.connect(lambda val=mem_line: self._copy_value("内存信息", val))
            hl.addWidget(mem_row)

        if dd.storage:
            for st in dd.storage:
                sp_pct = round(st.used * 100 / st.total) if st.total else 0
                st_summary = f"{fmt_size(st.used)} / {fmt_size(st.total)} ({sp_pct}%)"
                st_title = f"磁盘 ({st.mount})"
                st_row = _MetricRow(st_title, st_summary, sp_pct, f"{st.mount}: {st_summary}")
                st_row.clicked.connect(lambda t=st_title, val=f"{st.mount}: {st_summary}": self._copy_value(t, val))
                hl.addWidget(st_row)
        else:
            hl.addWidget(_InfoRow("磁盘", "—"))

        hl.addStretch(1)
        hw_card = _Card("硬件规格", hw_box, label("CPU · 内存 · 磁盘", "faint", px=11))

        grid = self._grid = W.QBoxLayout(W.QBoxLayout.Direction.LeftToRight)
        grid.setSpacing(12)
        grid.addWidget(sys_card, 1)
        grid.addWidget(hw_card, 1)
        self._info.addLayout(grid)
        self._relayout()

        # 卡片 4：刷机版本信息（独立全宽卡片）
        flash_box = W.QWidget()
        fl = W.QVBoxLayout(flash_box)
        fl.setContentsMargins(14, 10, 14, 12)
        fl.setSpacing(10)

        meta_grid = W.QGridLayout()
        meta_grid.setContentsMargins(0, 0, 0, 0)
        meta_grid.setHorizontalSpacing(20)
        meta_grid.setVerticalSpacing(2)
        meta_grid.setColumnStretch(0, 1)
        meta_grid.setColumnStretch(1, 1)

        flash_items = [
            ("Build ID", dash(dd.build_id)),
            ("编译时间", dash(dd.build_date)),
            ("安全补丁", dash(dd.security_patch)),
            ("Vendor 安全补丁", dash(dd.vendor_security_patch)),
            ("基带版本", dash(dd.baseband)),
            ("Bootloader", dash(dd.bootloader)),
        ]
        for idx, (k, v) in enumerate(flash_items):
            r = _InfoRow(k, v)
            r.clicked.connect(lambda key=k, val=v: self._copy_value(key, val))
            meta_grid.addWidget(r, idx // 2, idx % 2)
        fl.addLayout(meta_grid)

        fp_val = dash(dd.fingerprint)
        self.copy_fp_btn = ui.button("复制指纹", "ghost", "sm", icon="copy")
        self.copy_fp_btn.setToolTip("复制完整 ro.build.fingerprint")
        self.copy_fp_btn.clicked.connect(lambda _=False, val=fp_val: self._copy_value("编译指纹", val))
        fl.addWidget(hbox(label("编译指纹 (ro.build.fingerprint)", "muted"), None, self.copy_fp_btn, spacing=8))

        fp_box = W.QFrame()
        ui.repolish(fp_box, fpbox=True)
        fp_lay = W.QVBoxLayout(fp_box)
        fp_lay.setContentsMargins(10, 8, 10, 8)
        self.fp_lbl = label(fp_val, mono=True, wrap=True, select=True)
        fp_lay.addWidget(self.fp_lbl)
        fl.addWidget(fp_box)

        self.copy_flash_btn = tbtn("复制版本信息", "复制全部刷机版本信息（可直接贴入 Bug 单）", icon="copy")
        self.copy_flash_btn.clicked.connect(self._copy_flash_info)

        flash_actions: list[W.QWidget] = []
        if dd.build_type:
            flash_actions.append(ui.badge(dd.build_type, "info" if dd.build_type != "user" else "neutral", mono=True))
        flash_actions.append(self.copy_flash_btn)
        self._info.addWidget(_Card("刷机版本信息", flash_box, *flash_actions))

        self._flash_rows = [
            ("Build ID", dash(dd.build_id)),
            ("编译类型", dash(dd.build_type)),
            ("编译时间", dash(dd.build_date)),
            ("安全补丁", dash(dd.security_patch)),
            ("Vendor 安全补丁", dash(dd.vendor_security_patch)),
            ("基带版本", dash(dd.baseband)),
            ("Bootloader", dash(dd.bootloader)),
            ("编译指纹", fp_val),
        ]

        d = self.ctx.state.current
        disk_line = " · ".join(f"{s.mount} {fmt_size(s.used)}/{fmt_size(s.total)}" for s in dd.storage) or "—"
        head = f"设备: {d.display_name} ({d.serial})\n" if d else ""
        self._summary_text = "\n".join([
            f"{head}系统与安全: {android_val} · 内核 {dash(dd.kernel)} · {slot_val} · SELinux {se_val} · VB {vb_val} · 开机 {uptime_val}",
            f"硬件规格: {dash(dd.soc)} · {dash(dd.abi)} · {screen_val} · {codename_val} · 内存 {mem_line} · 磁盘 {disk_line}",
            f"刷机版本: {dash(dd.build_id)} · {dash(dd.build_type)} · 编译时间 {dash(dd.build_date)} · 安全补丁 {dash(dd.security_patch)}",
            f"编译指纹: {fp_val}",
        ])

    def _copy_value(self, title: str, value: str):
        if not value or value == "—":
            return
        QtWidgets.QApplication.clipboard().setText(value)
        self.ctx.notify("ok", f"已复制{title}", value)

    def _copy_flash_info(self):
        if not self._flash_rows:
            return
        d = self.ctx.state.current
        head = f"设备: {d.display_name} ({d.serial})\n" if d else ""
        text = head + "\n".join(f"{k}: {v}" for k, v in self._flash_rows)
        QtWidgets.QApplication.clipboard().setText(text)
        self.ctx.notify("ok", "已复制刷机版本信息", f"{len(self._flash_rows)} 项，可直接贴入 Bug 单")

    def _copy_summary(self):
        if not self._summary_text:
            return
        QtWidgets.QApplication.clipboard().setText(self._summary_text)
        self.ctx.notify("ok", "已复制设备摘要", "包含系统安全、硬件规格与刷机版本")

    # 拖放安装 --------------------------------------------------------
    def dragEnterEvent(self, e):
        d = self.ctx.state.current
        if d and d.is_adb and e.mimeData().hasUrls() and all(u.toLocalFile().lower().endswith(".apk") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        d = self.ctx.state.current
        if d and hasattr(self, "strip"):
            install_apks(self, d.serial, self.strip, [u.toLocalFile() for u in e.mimeData().urls()])
