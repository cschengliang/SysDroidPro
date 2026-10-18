"""设备总览：左侧设备列表；右侧 Root / Remount / Reboot 控制、版本信息、APEX 模块、状态。

Remount 流程（userdebug）：未 root 先 adb root → adb remount → 输出提示需要重启时询问一次 →
重启并等待设备回来 → 自动再执行 root + remount。每一步的命令与输出写在“操作输出”里。
"""
from __future__ import annotations

import time

import tbui as ui
from tbui._qt import QtWidgets, Qt

from ..backend.models import ApexModule, Device, DeviceDetail, RootState
from ..ui.common import (Column, DataTable, PanelHead, TaskStrip, fmt_duration, fmt_size, hbox, hline, label, output_log,
                         set_icon, tbtn, vline)
from .actions import REBOOT_TEXT, connect_dialog, install_apks, pair_dialog
from .base import Page

W = QtWidgets
PARTS = ["system", "system_ext", "product", "vendor", "odm"]


class _Card(W.QFrame):
    """带标题栏操作按钮的卡片。"""

    def __init__(self, title: str, content: W.QWidget, *actions):
        super().__init__()
        ui.repolish(self, card=True)
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.head = PanelHead(title, *actions)
        self.head.setStyleSheet("border-top-left-radius: 6px; border-top-right-radius: 6px;")
        lay.addWidget(self.head)
        lay.addWidget(content)


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
        left.setFixedWidth(300)
        ll = W.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        refresh = ui.icon_button("refresh-cw", "刷新设备列表")
        refresh.clicked.connect(self.ctx.state.refresh_devices)
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
        ll.addWidget(hline())
        server = tbtn("Restart adb", "adb kill-server + start-server", "ghost", icon="rotate-ccw")
        server.clicked.connect(self._restart_server)
        ll.addWidget(hbox(server, None, margins=(8, 6, 8, 6)))
        lay.addWidget(left)
        lay.addWidget(vline())

        self.scroll = W.QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        lay.addWidget(self.scroll, 1)

        self.detail: DeviceDetail | None = None
        self.root: RootState | None = None
        self._detail_serial: str | None = None
        self._token = 0
        self._busy = False                         # Root / Remount 流程进行中
        self._pending_remount: str | None = None   # 重启后需要继续 remount 的设备
        self._log = output_log(140)
        self._log.write("这里显示 Root / Remount / Reboot 实际执行的命令与输出。", "system")
        self._render(None)

        st = self.ctx.state
        st.devicesChanged.connect(self._devices)
        st.currentChanged.connect(self._current)
        st.rootChanged.connect(lambda s: s == self._detail_serial and self._load_root())
        self.list.deviceSelected.connect(st.select)
        self.setAcceptDrops(True)
        self._devices(st.devices)

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

    @staticmethod
    def _as_dict(d: Device) -> dict:
        state = "fastboot" if d.state == "bootloader" else d.state
        return dict(serial=d.serial, state=state, transport=d.transport, model=d.model or d.product or d.serial,
                    manufacturer=d.manufacturer, android=d.android, api=d.api, battery=d.battery)

    def _current(self, d: Device | None):
        if d and d.serial == self._pending_remount and d.state == "device":
            self._pending_remount = None
            self._log.write(f"设备已重新连接（{time.strftime('%H:%M:%S')}），继续 root + remount…", "system")
            if self._visible:
                self._render(d)
            self._remount_flow(d.serial, after_reboot=True)
            return
        if self._visible:
            self._render(d)

    def shown(self):
        d = self.ctx.state.current
        if d is None or d.serial != self._detail_serial or self.detail is None:
            self._render(d)

    def refresh(self):
        self.ctx.state.refresh_devices()
        self._render(self.ctx.state.current)

    def _restart_server(self):
        self.ctx.run(self.ctx.backend.restart_server,
                     done=lambda _: (self.ctx.notify("ok", "adb 服务已重启"), self.ctx.state.refresh_devices()))

    # 详情 ------------------------------------------------------------
    def _render(self, d: Device | None):
        self.detail = None
        self.root = None
        self._detail_serial = d.serial if d else None
        self._log.setParent(None)                  # 输出区跨设备保留，重绘前先摘下
        page = W.QWidget()
        page.setProperty("page", True)
        self.scroll.setWidget(page)
        v = W.QVBoxLayout(page)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(16)
        self._v = v
        if d is None:
            v.addWidget(ui.EmptyState("smartphone", "选择或连接一台设备",
                                      "左侧列出 adb 与 fastboot 识别到的全部设备。支持 USB、Wi-Fi 与模拟器。"))
            return

        name = W.QLabel(d.display_name)
        name.setFont(ui.theme().display_font(24))
        sub = [ui.DeviceStatus("fastboot" if d.state == "bootloader" else d.state), label(d.serial, "muted", mono=True)]
        if d.android:
            sub.append(ui.badge(f"Android {d.android}", mono=True))
        if d.api:
            sub.append(ui.badge(f"API {d.api}", mono=True))
        self._badges = hbox(*sub, None, spacing=6)
        col = W.QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(name)
        col.addWidget(self._badges)
        top = W.QHBoxLayout()
        top.addLayout(col, 1)
        top.addWidget(self._quick(d), 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        self.strip = TaskStrip()
        v.addWidget(self.strip)

        if d.state == "unauthorized":
            again = tbtn("Retry", "重新检测设备", icon="refresh-cw")
            again.clicked.connect(self.ctx.state.refresh_devices)
            v.addWidget(ui.Banner("warn", f"{d.display_name} 未授权",
                                  "请在手机上勾选“一律允许使用这台计算机进行调试”，然后点“允许”。", action=again))
        elif d.state in ("offline", "connecting", "authorizing"):
            v.addWidget(ui.Banner("info", "设备正在连接", "设备重启或 adbd 重启后会自动刷新。"))
        elif not d.is_adb:
            v.addWidget(ui.Banner("info", f"设备处于 {d.state.capitalize()} 模式",
                                  "可以用下方“重启”回到系统；刷写分区请使用 fastboot 命令行或“ADB 命令”页。"))

        v.addWidget(self._control_card(d))
        info = W.QWidget()                      # 版本信息、状态、APEX 加载后放进这里
        self._info = W.QVBoxLayout(info)
        self._info.setContentsMargins(0, 0, 0, 0)
        self._info.setSpacing(16)
        v.addWidget(info)

        if d.is_adb:
            self._loading = ui.ProgressRow("正在读取版本信息…", None)
            self._info.addWidget(self._loading)
            self._token += 1
            tok = self._token
            self.ctx.run(self.ctx.backend.device_detail, d.serial,
                         done=lambda dd: tok == self._token and self._got_detail(dd),
                         error=lambda e: tok == self._token and self._loading.set_value(100, str(e), failed=True))
            self._load_root()

        log_box = _Card("操作输出", self._log, self._clear_log_btn())
        v.addWidget(log_box)
        v.addStretch(1)

    def _clear_log_btn(self):
        b = ui.icon_button("eraser", "清空输出")
        b.clicked.connect(self._log.out.clear)
        return b

    def _quick(self, d: Device) -> W.QWidget:
        """标题右侧的快捷操作：统一为图标按钮，说明在 tooltip。"""
        adb = d.is_adb
        shot = ui.icon_button("camera", "截屏（保存到导出目录）", size="md")
        shot.clicked.connect(self._screenshot)
        mirror = ui.icon_button("cast", "投屏（scrcpy 新窗口）", size="md")
        mirror.clicked.connect(lambda: self.ctx.window.pages["mirror"].quick_start())
        shell = ui.icon_button("terminal", "打开 Shell", size="md")
        shell.clicked.connect(lambda: self.ctx.open_page("shell"))
        install = ui.icon_button("download", "安装 APK（也可以把 APK 拖进窗口）", size="md")
        install.clicked.connect(lambda: install_apks(self, d.serial, self.strip))
        items = [shot, mirror, shell, install]
        if d.transport == "wifi":
            disc = ui.icon_button("x", "断开无线连接", tone="danger", size="md")
            disc.clicked.connect(lambda: self.ctx.run(self.ctx.backend.disconnect, d.serial, done=lambda _: (
                self.ctx.notify("ok", "已断开", d.serial), self.ctx.state.refresh_devices())))
            items.append(disc)
        for b in (shot, mirror, shell, install):
            b.setEnabled(adb)
        return hbox(*items, spacing=2)

    # Root / Remount / Reboot ------------------------------------------
    def _control_card(self, d: Device) -> W.QWidget:
        """一行：按钮 + 状态，紧凑排列。"""
        box = W.QWidget()
        h = W.QHBoxLayout(box)
        h.setContentsMargins(12, 8, 12, 8)
        h.setSpacing(6)
        self.root_btn = tbtn("Root", "adb root / unroot", "primary", icon="shield", width=84)
        self.root_state_lbl = label("adbd: —", "muted", mono=True)
        self.remount_btn = tbtn("Remount", "adb remount：未 root 时自动先 root；需要关闭 verity 时询问一次，重启后自动继续",
                                "primary", icon="hard-drive", width=96)
        self.rw_state_lbl = label("/system: —", "muted", mono=True)
        self.reboot_btn = tbtn("Reboot", "重启系统", icon="rotate-ccw", width=84)
        self.reboot_btn.clicked.connect(lambda: self._reboot("system"))
        more = ui.icon_button("chevron-down", "更多重启方式：Recovery / Bootloader / Fastbootd / Sideload / 关机")
        more.setMenu(self._reboot_menu())
        more.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.verity_lbl = label("verity: —", "faint", mono=True)
        for w in (self.root_btn, self.root_state_lbl, 12, self.remount_btn, self.rw_state_lbl, 12,
                  self.reboot_btn, more, 12, self.verity_lbl):
            h.addSpacing(w) if isinstance(w, int) else h.addWidget(w)
        h.addStretch(1)
        self.root_btn.clicked.connect(self._toggle_root)
        self.remount_btn.clicked.connect(lambda: self._remount_flow(self._detail_serial))
        adb = d.is_adb
        self.root_btn.setEnabled(adb)
        self.remount_btn.setEnabled(adb)
        self.reboot_btn.setEnabled(d.state not in ("unauthorized", "offline", "connecting", "authorizing"))
        more.setEnabled(self.reboot_btn.isEnabled())
        return _Card("控制", box)

    def _reboot_menu(self):
        m = ui.menu([it for it in [("recovery", "重启到 Recovery", "hard-drive"), ("bootloader", "重启到 Bootloader", "zap"),
                                   ("fastboot", "重启到 Fastbootd", "zap"), ("sideload", "重启到 Sideload", "download"),
                                   None, ("poweroff", "关机", "power", None, True)]], self)
        m.triggered.connect(lambda a: self._reboot(a.data()))
        return m

    def _reboot(self, mode: str):
        d = self.ctx.state.current
        if d is None:
            return
        if mode == "poweroff" and not ui.confirm(self, "关闭设备？", f"{d.display_name} 将关机，需要手动开机后才能再次连接。", "关机", danger=True):
            return
        cmd = {"system": "adb reboot", "poweroff": "adb shell reboot -p"}.get(mode, f"adb reboot {mode}")
        self._log.write(cmd if d.is_adb else cmd.replace("adb", "fastboot"), "input")
        self.ctx.run(self.ctx.backend.reboot, d.serial, mode,
                     done=lambda _: (self._log.write(f"{REBOOT_TEXT[mode]}…", "system"),
                                     self.ctx.notify("info", f"{REBOOT_TEXT[mode]}…", d.display_name)),
                     error=lambda e: self._fail(e))

    def _load_root(self):
        serial = self._detail_serial
        if not serial:
            return
        self.ctx.run(self.ctx.backend.root_state, serial, done=lambda rs: serial == self._detail_serial and self._show_root(rs),
                     error=lambda e: None)

    def _show_root(self, rs: RootState):
        self.root = rs
        try:
            user_build = rs.build_type == "user"
            self.root_state_lbl.setText("adbd: root" if rs.adbd_root else "adbd: shell")
            ui.repolish(self.root_state_lbl, tone="ok" if rs.adbd_root else None)
            self.root_btn.setText("Unroot" if rs.adbd_root else "Root")
            self._variant(self.root_btn, "shield", primary=not rs.adbd_root)
            self.rw_state_lbl.setText("/system: rw" if rs.system_writable else "/system: ro" if rs.system_writable is False else "/system: —")
            ui.repolish(self.rw_state_lbl, tone="ok" if rs.system_writable else None)
            self._variant(self.remount_btn, "hard-drive", primary=not rs.system_writable)
            self.verity_lbl.setText("verity: " + ("on" if rs.verity_enabled else "off" if rs.verity_enabled is False else "—"))
            if user_build:
                for b in (self.root_btn, self.remount_btn):
                    b.setEnabled(False)
                    b.setToolTip("user 版本固件不支持 adb root / remount")
                self.root_state_lbl.setText("adbd: user build")
            for b in (self.root_btn, self.remount_btn):
                b.setEnabled(b.isEnabled() and not self._busy)
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
        try:
            for b in (self.root_btn, self.remount_btn):
                b.setEnabled(not on and (self.root is None or self.root.build_type != "user"))
        except RuntimeError:
            pass

    def _fail(self, e):
        self._log.write(f"失败：{e}" + (f"\n{e.detail.strip()}" if getattr(e, "detail", "").strip() else ""), "error")
        self.ctx.report(e)

    def _toggle_root(self):
        serial = self._detail_serial
        if not serial or self._busy:
            return
        enable = not (self.root and self.root.adbd_root)
        self._set_busy(True)
        self._log.write("adb root" if enable else "adb unroot", "input")

        def done(out):
            self._set_busy(False)
            self._log.write(out.strip() or "完成", "output")
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
            self._log.write(item[1], item[0])

        def done(res):
            self._set_busy(False)
            self._log.write(res.output.strip(), "output")
            self.ctx.state.rootChanged.emit(serial)
            if res.needs_reboot:
                if after_reboot:
                    self._log.write("重启后仍提示需要重启，请检查 remount 输出。", "error")
                    return
                if ui.confirm(self, "需要重启后生效", "remount 已关闭 verity / 启用 overlayfs，需要重启设备后 /system 才可写。<br>"
                              "立即重启？设备回来后会自动再执行 root + remount。", "立即重启"):
                    self._pending_remount = serial
                    self._log.write("adb reboot", "input")
                    self.ctx.run(b.reboot, serial, "system", done=lambda _: self._log.write("等待设备重新连接…", "system"),
                                 error=self._fail)
                else:
                    self._log.write("已取消重启。稍后手动重启后再点 Remount。", "system")
            else:
                self.ctx.notify("ok", "/system 已可写", "可以 adb push 修改后的模块了。")

        def fail(e):
            self._set_busy(False)
            self._fail(e)
            self._load_root()
        self.ctx.run(work, done=done, error=fail, progress=step)

    # 版本信息 --------------------------------------------------------
    def _got_detail(self, dd: DeviceDetail):
        self.detail = dd
        self._loading.setParent(None)
        self._loading.deleteLater()
        lay = self._badges.layout()
        if dd.build_type:
            lay.insertWidget(lay.count() - 1, ui.badge(dd.build_type, "info" if dd.build_type != "user" else "neutral", mono=True))

        def dash(x):
            return "—" if x in (None, "") else str(x)

        rows = [
            ("Android 版本 / API", f"{dd.android}（API {dd.api}）" if dd.android else "—"),
            ("Build ID / 类型", " · ".join(x for x in (dd.build_id, dd.build_type) if x) or "—"),
            ("构建指纹", dash(dd.fingerprint)),
            ("构建时间", dash(dd.build_date)),
            ("安全补丁", dash(dd.security_patch)),
            ("Vendor 安全补丁", dash(dd.vendor_security_patch)),
            ("基带版本", dash(dd.baseband)),
            ("Bootloader", dash(dd.bootloader)),
            ("内核版本", dash(dd.kernel)),
            ("平台 / SoC", " · ".join(x for x in (dd.soc, dd.abi) if x) or "—"),
            ("当前槽位", dash(dd.slot)),
            ("Verified Boot", dash(dd.verified_boot)),
            ("SELinux", dash(dd.selinux)),
        ]
        ref = dd.partition_fingerprints.get("system")
        mismatch = []
        for p in PARTS:
            fp = dd.partition_fingerprints.get(p)
            if fp:
                bad = ref is not None and fp != ref
                rows.append((f"指纹 · {p}", fp + ("    ≠ system" if bad else "")))
                if bad:
                    mismatch.append(len(rows) - 1)
        self._version_rows = rows
        props = ui.PropertyList(rows)
        for r in mismatch:
            props.item(r, 1).setForeground(ui.theme().color("warn"))
            props.item(r, 1).setToolTip("该分区指纹与 system 不一致（可能单独刷过这个分区）")
        if dd.kernel_full:
            props.item(8, 1).setToolTip(dd.kernel_full)
        copy_all = tbtn("Copy all", "按“项目: 值”复制全部版本信息（可直接贴进 bug 单）；双击单行复制该值", icon="copy")
        copy_all.clicked.connect(self._copy_versions)
        version_card = _Card("版本信息", props, copy_all)

        status = W.QWidget()
        sl = W.QVBoxLayout(status)
        sl.setContentsMargins(16, 16, 16, 16)
        sl.setSpacing(18)
        bat = dd.battery
        if bat:
            bits = [x for x in (bat.status, f"{bat.temperature_c:.1f} °C" if bat.temperature_c is not None else None,
                                f"{bat.voltage_v:.2f} V" if bat.voltage_v is not None else None,
                                f"{bat.current_ma:+d} mA" if bat.current_ma is not None else None) if x]
            sl.addWidget(ui.ProgressRow("电量", bat.level or 0, " · ".join(bits)))
        if dd.mem_total:
            used = dd.mem_total - (dd.mem_available or 0)
            sl.addWidget(ui.ProgressRow("内存", round(used * 100 / dd.mem_total), f"{fmt_size(used)} / {fmt_size(dd.mem_total)} 已用"))
        for s in dd.storage:
            sl.addWidget(ui.ProgressRow(f"存储 {s.mount}", round(s.used * 100 / s.total) if s.total else 0,
                                        f"{fmt_size(s.used)} / {fmt_size(s.total)} 已用"))
        sl.addWidget(label(f"开机时长 {fmt_duration(dd.uptime_s)}", "faint", mono=True, px=11))
        sl.addStretch(1)

        grid = W.QHBoxLayout()
        grid.setSpacing(16)
        grid.addWidget(version_card, 8, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(ui.card("状态", status), 4, Qt.AlignmentFlag.AlignTop)
        self._info.addLayout(grid)
        self._info.addWidget(self._apex_card())

    def _copy_versions(self):
        d = self.ctx.state.current
        head = f"设备: {d.display_name} ({d.serial})\n" if d else ""
        text = head + "\n".join(f"{k}: {v.replace('    ≠ system', '（与 system 不一致）')}" for k, v in self._version_rows)
        QtWidgets.QApplication.clipboard().setText(text)
        self.ctx.notify("ok", "已复制版本信息", f"{len(self._version_rows)} 项，可以直接贴进 bug 单")

    # APEX ----------------------------------------------------------
    def _apex_card(self) -> W.QWidget:
        box = W.QWidget()
        bl = W.QVBoxLayout(box)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(0)
        self.apex_filter = ui.FilterField("Filter: module", width=220)
        toggle = self.apex_toggle = tbtn("Show", "展开 / 收起 APEX 模块列表", icon="chevron-down", width=76)
        card = _Card("Mainline / APEX 模块", box, self.apex_filter, toggle)
        self.apex_table = DataTable([
            Column("模块", lambda m: m.name, 320, mono=True),
            Column("版本", lambda m: m.version_code, 140, mono=True, align="right", sort=lambda m: m.version_code or 0),
            Column("路径", lambda m: m.path, None, mono=True, tone=lambda m: "ink-muted"),
        ])
        self.apex_table.setMinimumHeight(300)
        self.apex_table.hide()
        self.apex_filter.hide()
        bl.addWidget(self.apex_table)
        self.apex_filter.textChanged.connect(lambda s: (self.apex_table.filter.set_text(s),
                                                         self.apex_filter.set_count(self.apex_table.filter.rowCount(), self.apex_table.table_model.rowCount())))

        def flip():
            show = self.apex_table.isHidden()
            self.apex_table.setVisible(show)
            self.apex_filter.setVisible(show)
            toggle.setText("Hide" if show else "Show")
            if show and not self.apex_table.table_model.rowCount() and self._detail_serial:
                serial = self._detail_serial
                self.ctx.run(self.ctx.backend.list_apex_modules, serial,
                             done=lambda mods: serial == self._detail_serial and self._got_apex(card, mods))
        toggle.clicked.connect(flip)
        return card

    def _got_apex(self, card, mods: list[ApexModule]):
        self.apex_table.set_rows(mods)
        card.head.set_count(len(mods))

    # 截屏 ------------------------------------------------------------
    def _screenshot(self):
        d = self.ctx.state.current
        if d is None:
            return
        self._log.write("adb exec-out screencap -p", "input")

        def done(png: bytes):
            p = self.ctx.settings.export_dir() / "screenshots"
            p.mkdir(parents=True, exist_ok=True)
            path = p / f"Screenshot_{d.serial.replace(':', '_')}_{time.strftime('%Y%m%d-%H%M%S')}.png"
            path.write_bytes(png)
            self._log.write(f"已保存 {path}", "output")
            self.ctx.notify("ok", "截图已保存", str(path))
        self.ctx.run(self.ctx.backend.screenshot, d.serial, done=done, error=self._fail)

    # 拖放安装 --------------------------------------------------------
    def dragEnterEvent(self, e):
        d = self.ctx.state.current
        if d and d.is_adb and e.mimeData().hasUrls() and all(u.toLocalFile().lower().endswith(".apk") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        d = self.ctx.state.current
        if d and hasattr(self, "strip"):
            install_apks(self, d.serial, self.strip, [u.toLocalFile() for u in e.mimeData().urls()])
