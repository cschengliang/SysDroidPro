"""日志抓取：多选缓冲区、容量设置（32/128/256 MB）、清理缓冲区、直写落盘与连接 ADB 自动抓取，
以及默认折叠的 screenrecord 扩展功能（--bugreport / --bit-rate / --display-id）。
"""
from __future__ import annotations

import time
from pathlib import Path

import tbui as ui
from tbui._qt import QtCore, QtWidgets, Qt

from ..backend.models import Device, DisplayInfo, LogcatBufferInfo
from ..core.log_capture import ALL_BUFFERS, DEFAULT_BUFFERS, buffer_summary
from ..ui.common import BusyBar, PanelHead, fmt_duration, fmt_size, hbox, hline, label, reveal, set_icon, tbtn
from .base import Page

W = QtWidgets
MB = 1024 * 1024
BUFFER_SIZES_MB = (32, 128, 256)
BITRATES = [
    ("4000000", "4 Mbps"),
    ("8000000", "8 Mbps"),
    ("12000000", "12 Mbps"),
    ("16000000", "16 Mbps"),
    ("20000000", "20 Mbps"),
]


class LogCapturePage(Page):
    id = "log_capture"
    title = "日志抓取"
    icon = "circle"

    def build(self):
        outer = W.QVBoxLayout(self.body)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        ctrl = self.ctx.log_capture
        s = self.ctx.settings

        # ── 顶部单行工具栏 ────────────────────────────────────
        tb = ui.Toolbar()
        self.capture_btn = tb.add(tbtn("开始抓取", "开始 / 停止抓取 logcat 到本地文件", "primary"))
        set_icon(self.capture_btn, "circle", "on-signal", hover="on-signal")
        self.dump_btn = tb.add(tbtn("导出缓存", "执行 adb logcat -d，立即导出设备当前缓冲区已有日志", icon="download"))
        tb.separator()
        self.clear_btn = tb.add(tbtn("清理缓冲区", "执行 adb logcat -c 清理当前已勾选的缓冲区", icon="eraser"))
        self.status_badge = tb.add(ui.badge("待机", "neutral", mono=True))
        tb.stretch()
        self.sw_auto = tb.add(ui.Switch("连接 ADB 自动抓取", bool(s.get("log_capture_auto"))))
        self.sw_auto.setToolTip("开启后，扫描发现新接入或重连的当前设备时自动抓取 logcat")
        outer.addWidget(tb)

        self.busy = BusyBar()
        outer.addWidget(self.busy)

        # ── 主内容区 ──────────────────────────────────────────
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        outer.addWidget(scroll, 1)

        page = W.QWidget()
        page.setProperty("page", True)
        scroll.setWidget(page)
        v = W.QVBoxLayout(page)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        # 1. 抓取配置卡片（缓冲区多选 + 容量设置）
        v.addWidget(self._build_config_card())

        # 2. 最近一次抓取卡片
        v.addWidget(self._build_last_log_card())

        # 3. 屏幕录制扩展卡片（默认折叠）
        v.addWidget(self._build_video_card())
        v.addStretch(1)

        # 状态与事件连接 ------------------------------------------------
        self._buf_infos: dict[str, LogcatBufferInfo] = {}
        self._requested_mb: int | None = None
        self._displays_loaded = False
        self._video_expanded = False

        self._tick_timer = QtCore.QTimer(self)
        self._tick_timer.setInterval(500)
        self._tick_timer.timeout.connect(self._on_tick)

        self.capture_btn.clicked.connect(self._toggle_capture)
        self.dump_btn.clicked.connect(self._dump_now)
        self.clear_btn.clicked.connect(self._clear_buffers)
        self.sw_auto.toggled.connect(lambda on: s.set("log_capture_auto", bool(on)))

        ctrl.stateChanged.connect(self._sync_ui)
        s.changed.connect(self._on_setting)
        self._sync_buffers_ui()
        self._sync_ui()

    def _build_config_card(self) -> W.QWidget:
        s = self.ctx.settings

        card = ui.GlassCard()
        lay = W.QVBoxLayout(card)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(10)

        # 行 1：缓冲区多选
        row1 = W.QHBoxLayout()
        row1.setSpacing(12)
        lbl1 = label("缓冲区", "muted")
        lbl1.setFixedWidth(64)
        row1.addWidget(lbl1)

        self.buf_buttons: dict[str, W.QPushButton] = {}
        pills = W.QHBoxLayout()
        pills.setSpacing(6)
        for name in ALL_BUFFERS:
            b = ui.button(name, size="sm")
            b.setCheckable(True)
            b.setFont(ui.theme().mono_font(12, 600))
            b.clicked.connect(lambda _=False, n=name: self._on_buffer_toggled(n))
            self.buf_buttons[name] = b
            pills.addWidget(b)
        row1.addLayout(pills)
        row1.addStretch(1)

        self.select_all_btn = ui.button("全选", size="sm")
        self.select_all_btn.clicked.connect(self._toggle_select_all)
        row1.addWidget(self.select_all_btn)
        lay.addLayout(row1)

        lay.addWidget(hline())

        # 行 2：缓冲大小 + 抓取前自动清理
        row2 = W.QHBoxLayout()
        row2.setSpacing(12)
        lbl2 = label("缓冲大小", "muted")
        lbl2.setFixedWidth(64)
        row2.addWidget(lbl2)

        self.size_buttons: dict[int, W.QPushButton] = {}
        size_box = W.QHBoxLayout()
        size_box.setSpacing(4)
        for mb in BUFFER_SIZES_MB:
            b = ui.button(f"{mb} MB", size="sm")
            b.setCheckable(True)
            b.setFont(ui.theme().mono_font(12, 600))
            b.setToolTip(f"将已选缓冲区容量设为 {mb} MB（adb logcat -G {mb}M）")
            b.clicked.connect(lambda _=False, m=mb: self._set_buffer_size(m))
            self.size_buttons[mb] = b
            size_box.addWidget(b)
        row2.addLayout(size_box)

        self.refresh_g_btn = ui.icon_button("refresh-cw", "查询当前缓冲区容量（adb logcat -g）")
        self.refresh_g_btn.clicked.connect(self.refresh)
        row2.addWidget(self.refresh_g_btn)

        self.size_readout = label("当前: —", "muted", mono=True)
        row2.addWidget(self.size_readout)
        row2.addStretch(1)

        self.sw_clear_before = ui.Switch("抓取前自动清理", bool(s.get("log_capture_clear_before")))
        self.sw_clear_before.setToolTip("每次点击“开始抓取”或触发自动抓取前先执行 logcat -c")
        self.sw_clear_before.toggled.connect(lambda on: s.set("log_capture_clear_before", bool(on)))
        row2.addWidget(self.sw_clear_before)
        lay.addLayout(row2)
        return card

    def _build_last_log_card(self) -> W.QWidget:
        card = ui.GlassCard()
        lay = W.QVBoxLayout(card)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        self.last_log_badge = ui.badge("暂无记录", "neutral", mono=True)
        lay.addWidget(PanelHead("最近一次抓取", self.last_log_badge))

        body = W.QWidget()
        bl = W.QVBoxLayout(body)
        bl.setContentsMargins(16, 12, 16, 12)
        bl.setSpacing(8)

        self.last_log_path = W.QLineEdit()
        self.last_log_path.setReadOnly(True)
        self.last_log_path.setFont(ui.theme().mono_font(12))
        self.last_log_path.setPlaceholderText("完成抓取或导出缓存后，这里将显示日志路径")

        self.copy_log_btn = tbtn("复制路径", "复制最近一次成功抓取的日志文件路径", "primary", icon="copy")
        self.copy_log_btn.clicked.connect(self._copy_log_path)
        self.open_log_dir_btn = ui.icon_button("folder-open", "打开日志导出目录", size="md")
        self.open_log_dir_btn.clicked.connect(self._open_log_dir)

        bl.addWidget(hbox(self.last_log_path, self.copy_log_btn, self.open_log_dir_btn, spacing=8))

        self.active_log_hint = label("", "faint", mono=True)
        self.active_log_hint.hide()
        bl.addWidget(self.active_log_hint)
        lay.addWidget(body)
        return card

    def _build_video_card(self) -> W.QWidget:
        s = self.ctx.settings
        card = ui.GlassCard()
        lay = W.QVBoxLayout(card)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)

        # 折叠标题栏
        head = W.QFrame()
        ui.repolish(head, panelhead=True)
        head.setFixedHeight(40)
        hl = W.QHBoxLayout(head)
        hl.setContentsMargins(8, 0, 8, 0)
        hl.setSpacing(8)

        self.video_expand_btn = ui.button("屏幕录制", "ghost", "sm")
        set_icon(self.video_expand_btn, "chevron-right", "ink-muted", 16)
        self.video_expand_btn.setToolTip("展开 / 收起屏幕录制扩展参数（screenrecord）")
        self.video_expand_btn.clicked.connect(self._toggle_video_card)
        hl.addWidget(self.video_expand_btn)

        hl.addWidget(ui.badge("扩展", "neutral"))
        self.video_status_badge = ui.badge("待机", "neutral", mono=True)
        hl.addWidget(self.video_status_badge)
        hl.addStretch(1)

        self.sw_sync_video = ui.Switch("随日志同步录屏", bool(s.get("log_capture_sync_video")))
        self.sw_sync_video.setToolTip("开启后，开始 / 停止抓取 logcat 时同步开始 / 停止录屏")
        self.sw_sync_video.toggled.connect(lambda on: s.set("log_capture_sync_video", bool(on)))
        hl.addWidget(self.sw_sync_video)

        self.video_btn = tbtn("开始录屏", "启动或停止 adb shell screenrecord 录屏")
        set_icon(self.video_btn, "video", "danger", hover="danger")
        self.video_btn.clicked.connect(self._toggle_video)
        hl.addWidget(self.video_btn)
        lay.addWidget(head)

        # 折叠内容（默认隐藏）
        self.video_body = W.QWidget()
        vl = W.QVBoxLayout(self.video_body)
        vl.setContentsMargins(16, 12, 16, 12)
        vl.setSpacing(10)

        # 参数行：--bugreport / --bit-rate / --display-id
        params = W.QHBoxLayout()
        params.setSpacing(12)

        self.sw_bugreport = ui.Switch("--bugreport", bool(s.get("log_capture_bugreport")))
        self.sw_bugreport.setFont(ui.theme().mono_font(12, 600))
        self.sw_bugreport.setToolTip("在画面叠加时间戳与帧号水印，便于与 logcat 对齐排查")
        self.sw_bugreport.toggled.connect(lambda on: s.set("log_capture_bugreport", bool(on)))
        params.addWidget(self.sw_bugreport)

        params.addWidget(label("码率", "muted"))
        self.bitrate_combo = ui.select(BITRATES, str(s.get("log_capture_bitrate")), size="sm", mono=True)
        self.bitrate_combo.setToolTip("视频码率（--bit-rate <bps>）")
        self.bitrate_combo.currentIndexChanged.connect(
            lambda *_: s.set("log_capture_bitrate", int(self.bitrate_combo.currentData()))
        )
        params.addWidget(self.bitrate_combo)

        params.addWidget(label("Display ID", "muted"))
        self.display_combo = W.QComboBox()
        self.display_combo.setEditable(True)
        ui.repolish(self.display_combo, size="sm")
        self.display_combo.setMinimumWidth(200)
        self.display_combo.setFont(ui.theme().mono_font(12))
        self.display_combo.lineEdit().setFont(ui.theme().mono_font(12))
        self.display_combo.lineEdit().setPlaceholderText("默认主屏（可选 ID）")
        self.display_combo.setEditText(str(s.get("log_capture_display_id") or ""))
        self.display_combo.setToolTip("可选：指定物理或虚拟屏幕 ID（--display-id <ID>），留空使用主屏")
        self.display_combo.currentTextChanged.connect(self._on_display_changed)
        params.addWidget(self.display_combo, 1)

        self.refresh_displays_btn = ui.icon_button("refresh-cw", "查询设备 Display ID（dumpsys SurfaceFlinger --display-id）")
        self.refresh_displays_btn.clicked.connect(lambda: self._load_displays(force=True))
        params.addWidget(self.refresh_displays_btn)
        vl.addLayout(params)

        vl.addWidget(hline())

        # 最近一次录屏结果行
        self.last_video_badge = ui.badge("暂无记录", "neutral", mono=True)
        vl.addWidget(hbox(label("最近录屏", "muted"), None, self.last_video_badge))

        self.last_video_path = W.QLineEdit()
        self.last_video_path.setReadOnly(True)
        self.last_video_path.setFont(ui.theme().mono_font(12))
        self.last_video_path.setPlaceholderText("停止录屏后将自动拉取并显示本地 MP4 路径")

        self.copy_video_btn = tbtn("复制路径", "复制最近一次成功保存的录屏文件路径", icon="copy")
        self.copy_video_btn.clicked.connect(self._copy_video_path)
        self.open_video_dir_btn = ui.icon_button("folder-open", "打开录屏导出目录", size="md")
        self.open_video_dir_btn.clicked.connect(self._open_log_dir)

        vl.addWidget(hbox(self.last_video_path, self.copy_video_btn, self.open_video_dir_btn, spacing=8))

        self.active_video_hint = label("", "faint", mono=True)
        self.active_video_hint.hide()
        vl.addWidget(self.active_video_hint)

        self.video_body.hide()
        lay.addWidget(self.video_body)
        return card

    # 生命周期 --------------------------------------------------------
    def load(self, device: Device):
        self._buf_infos.clear()
        self._requested_mb = None
        self._displays_loaded = False
        self._load_buffer_sizes(device.serial)
        if self._video_expanded:
            self._load_displays()
        self._sync_ui()

    def unload(self):
        self._tick_timer.stop()
        self._buf_infos.clear()
        self._requested_mb = None
        self._displays_loaded = False
        self._clear_display_items()
        self._sync_size_ui()
        self._sync_ui()

    def refresh(self):
        if self.device is None:
            return
        self._load_buffer_sizes(self.serial)
        if self._video_expanded:
            self._load_displays(force=True)

    def shown(self):
        self._sync_timer()
        self._sync_ui()

    def hidden(self):
        self._tick_timer.stop()

    def status_text(self) -> str:
        ctrl = self.ctx.log_capture
        parts = []
        if ctrl.log_recording:
            elapsed = fmt_duration(max(0.0, time.monotonic() - ctrl.log_started_at))
            parts.append(f"录制中 {elapsed} · {fmt_size(ctrl.log_bytes)}")
        if ctrl.video_recording:
            v_elapsed = fmt_duration(max(0.0, time.monotonic() - ctrl.video_started_at))
            parts.append(f"录屏中 {v_elapsed}")
        return " · ".join(parts) if parts else f"待机 · {len(ctrl.selected_buffers())} 个缓冲区"

    # 缓冲区与设置操作 ------------------------------------------------
    def _on_buffer_toggled(self, name: str):
        ctrl = self.ctx.log_capture
        if ctrl.log_recording:
            self._sync_buffers_ui()
            self.ctx.notify("warn", "正在录制中", "请先停止抓取再调整缓冲区。")
            return
        cur = set(ctrl.selected_buffers())
        if name in cur:
            cur.remove(name)
        else:
            cur.add(name)
        self._requested_mb = None
        ctrl.set_selected_buffers(list(cur))

    def _toggle_select_all(self):
        ctrl = self.ctx.log_capture
        if ctrl.log_recording:
            self.ctx.notify("warn", "正在录制中", "请先停止抓取再调整缓冲区。")
            return
        cur = set(ctrl.selected_buffers())
        self._requested_mb = None
        if len(cur) == len(ALL_BUFFERS):
            ctrl.set_selected_buffers(list(DEFAULT_BUFFERS))
        else:
            ctrl.set_selected_buffers(list(ALL_BUFFERS))

    def _load_buffer_sizes(self, serial: str):
        self.request(
            "buf_sizes",
            self.ctx.backend.get_logcat_buffer_sizes,
            serial,
            busy=self.busy,
            done=self._got_buffer_sizes,
            error=lambda e: self.size_readout.setText("当前: 查询失败"),
        )

    def _got_buffer_sizes(self, infos: list[LogcatBufferInfo]):
        self._buf_infos = {info.name: info for info in infos}
        self._sync_size_ui()

    def _set_buffer_size(self, size_mb: int):
        if self.device is None:
            return
        buffers = self.ctx.log_capture.selected_buffers()
        if not buffers:
            self._sync_size_ui()
            self.ctx.notify("warn", "请先选择缓冲区", "至少勾选一个日志缓冲区后再设置容量。")
            return
        self._requested_mb = size_mb
        self._sync_size_ui()

        def done(infos: list[LogcatBufferInfo]):
            for info in infos:
                self._buf_infos[info.name] = info
            self._sync_size_ui()
            clamped = [
                info for info in infos
                if info.size_bytes is not None and info.size_bytes < size_mb * MB
            ]
            if clamped:
                detail = "，".join(f"{i.name}={fmt_size(i.size_bytes)}" for i in clamped)
                self.ctx.notify("warn", f"已设置 {size_mb} MB（部分受设备上限限制）", detail)
            else:
                self.ctx.notify("ok", f"缓冲区大小已设为 {size_mb} MB", "已通过 logcat -g 校验生效")

        self.request(
            "set_buf_size",
            self.ctx.backend.set_logcat_buffer_size,
            self.serial,
            size_mb,
            buffers,
            busy=self.busy,
            done=done,
            error=lambda e: (self._sync_size_ui(), self.ctx.report(e, "设置缓冲区大小失败")),
        )

    def _sync_buffers_ui(self):
        selected = set(self.ctx.log_capture.selected_buffers())
        for name, btn in self.buf_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(name in selected)
            btn.blockSignals(False)
        self.select_all_btn.setText("恢复默认" if len(selected) == len(ALL_BUFFERS) else "全选")
        self._sync_size_ui()

    def _sync_size_ui(self):
        selected = self.ctx.log_capture.selected_buffers()
        if not selected:
            for btn in self.size_buttons.values():
                btn.blockSignals(True)
                btn.setChecked(False)
                btn.blockSignals(False)
            self.size_readout.setText("请至少选择 1 个缓冲区")
            ui.repolish(self.size_readout, tone="danger")
            return

        infos = [self._buf_infos[b] for b in selected if b in self._buf_infos]
        mbs = {round(i.size_bytes / MB) for i in infos if i.size_bytes is not None}
        common_mb = next(iter(mbs)) if len(mbs) == 1 and len(infos) == len(selected) else None
        for mb, btn in self.size_buttons.items():
            btn.blockSignals(True)
            btn.setChecked(common_mb == mb)
            btn.blockSignals(False)

        if not infos:
            self.size_readout.setText("当前: —")
            ui.repolish(self.size_readout, tone=None)
            return

        req = self._requested_mb
        clamped = [i for i in infos if req is not None and i.size_bytes is not None and i.size_bytes < req * MB]
        if clamped:
            parts = ", ".join(f"{i.name} {round(i.size_bytes / MB)}M" for i in clamped)
            self.size_readout.setText(f"当前: {req} MB（受限: {parts}）")
            ui.repolish(self.size_readout, tone="warn")
        elif common_mb is not None:
            self.size_readout.setText(f"当前: {common_mb} MB")
            ui.repolish(self.size_readout, tone=None)
        else:
            summary = " · ".join(f"{i.name} {round(i.size_bytes / MB)}M" for i in infos if i.size_bytes is not None)
            self.size_readout.setText(f"当前: {summary or '—'}")
            ui.repolish(self.size_readout, tone=None)

    # 抓取与导出 ------------------------------------------------------
    def _toggle_capture(self):
        ctrl = self.ctx.log_capture
        if ctrl.log_recording:
            ctrl.stop_log_capture()
        elif self.device is not None:
            ctrl.start_log_capture(self.serial, reason="手动开始")

    def _dump_now(self):
        if self.device is None:
            return
        self.busy.push()
        job = self.ctx.log_capture.dump_log_now(
            self.serial,
            on_done=lambda _res: self.busy.pop(),
            on_error=lambda e: (self.busy.pop(), self.ctx.report(e, "导出缓存失败")),
        )
        if job is None:
            self.busy.pop()

    def _clear_buffers(self):
        if self.device is None:
            return
        buffers = self.ctx.log_capture.selected_buffers()
        if not buffers:
            self.ctx.notify("warn", "请先选择缓冲区")
            return

        def done(_):
            for b in buffers:
                if b in self._buf_infos:
                    info = self._buf_infos[b]
                    self._buf_infos[b] = LogcatBufferInfo(info.name, info.size_bytes, 0)
            self.ctx.notify("ok", "已清理日志缓冲区", ", ".join(buffers))

        self.run_device(self.ctx.backend.clear_logcat, self.serial, buffers, done=done)

    # 屏幕录制（扩展卡片）---------------------------------------------
    def _toggle_video_card(self):
        self._video_expanded = not self._video_expanded
        self.video_body.setVisible(self._video_expanded)
        set_icon(self.video_expand_btn, "chevron-down" if self._video_expanded else "chevron-right", "ink-muted", 16)
        if self._video_expanded and self.device is not None and not self._displays_loaded:
            self._load_displays()

    def _toggle_video(self):
        ctrl = self.ctx.log_capture
        if ctrl.video_recording:
            ctrl.stop_screenrecord()
        elif self.device is not None:
            ctrl.start_screenrecord(self.serial, reason="手动录屏")

    def _load_displays(self, force: bool = False):
        if self.device is None or (self._displays_loaded and not force):
            return
        self._displays_loaded = True
        self.request(
            "displays",
            self.ctx.backend.list_displays,
            self.serial,
            busy=self.busy,
            done=self._got_displays,
            error=lambda e: self.ctx.report(e, "读取 Display 列表失败"),
        )

    def _clear_display_items(self):
        saved = self.display_combo.currentText()
        self.display_combo.blockSignals(True)
        self.display_combo.clear()
        self.display_combo.setEditText(saved)
        self.display_combo.blockSignals(False)

    def _got_displays(self, displays: list[DisplayInfo]):
        saved = self.ctx.settings.get("log_capture_display_id")
        self.display_combo.blockSignals(True)
        self.display_combo.clear()
        for i, d in enumerate(displays):
            self.display_combo.addItem(d.id, d.id)
            if d.name:
                self.display_combo.setItemData(i, f"{d.id} · {d.name}", Qt.ItemDataRole.ToolTipRole)
        self.display_combo.setEditText(str(saved or ""))
        self.display_combo.blockSignals(False)

    def _on_display_changed(self, text: str):
        self.ctx.settings.set("log_capture_display_id", text.strip())

    # 路径操作 --------------------------------------------------------
    def _copy_log_path(self):
        rec = self.ctx.log_capture.last_log
        if rec is None:
            return
        W.QApplication.clipboard().setText(rec.path)
        self.ctx.notify("ok", "已复制日志路径", rec.path)

    def _copy_video_path(self):
        rec = self.ctx.log_capture.last_video
        if rec is None:
            return
        W.QApplication.clipboard().setText(rec.path)
        self.ctx.notify("ok", "已复制录屏路径", rec.path)

    def _open_log_dir(self):
        target_dir = self.ctx.settings.export_dir() / "logcat"
        target_dir.mkdir(parents=True, exist_ok=True)
        reveal(str(target_dir))

    # 同步刷新 --------------------------------------------------------
    def _on_setting(self, key: str):
        s = self.ctx.settings
        if key == "log_capture_buffers":
            self._sync_buffers_ui()
        elif key == "log_capture_auto":
            self.sw_auto.blockSignals(True)
            self.sw_auto.setChecked(bool(s.get(key)))
            self.sw_auto.blockSignals(False)
        elif key == "log_capture_clear_before":
            self.sw_clear_before.blockSignals(True)
            self.sw_clear_before.setChecked(bool(s.get(key)))
            self.sw_clear_before.blockSignals(False)
        elif key == "log_capture_sync_video":
            self.sw_sync_video.blockSignals(True)
            self.sw_sync_video.setChecked(bool(s.get(key)))
            self.sw_sync_video.blockSignals(False)
        elif key == "log_capture_bugreport":
            self.sw_bugreport.blockSignals(True)
            self.sw_bugreport.setChecked(bool(s.get(key)))
            self.sw_bugreport.blockSignals(False)
        elif key == "log_capture_bitrate":
            self.bitrate_combo.blockSignals(True)
            idx = self.bitrate_combo.findData(str(s.get(key)))
            if idx >= 0:
                self.bitrate_combo.setCurrentIndex(idx)
            self.bitrate_combo.blockSignals(False)
        elif key == "log_capture_display_id":
            val = str(s.get(key) or "")
            if self.display_combo.currentText().strip() != val:
                self.display_combo.blockSignals(True)
                self.display_combo.setEditText(val)
                self.display_combo.blockSignals(False)

    def _on_tick(self):
        self._sync_live_labels()
        self.ctx.window.update_status(self)

    def _sync_timer(self):
        if self._visible and self.ctx.log_capture.any_recording:
            if not self._tick_timer.isActive():
                self._tick_timer.start()
        else:
            self._tick_timer.stop()

    def _sync_live_labels(self):
        ctrl = self.ctx.log_capture
        if ctrl.log_recording:
            elapsed = fmt_duration(max(0.0, time.monotonic() - ctrl.log_started_at))
            self.status_badge.setText("正在停止…" if ctrl.log_stopping else f"● {elapsed} · {fmt_size(ctrl.log_bytes)}")
            ui.repolish(self.status_badge, badge="danger")
        else:
            self.status_badge.setText("待机")
            ui.repolish(self.status_badge, badge="neutral")

        if ctrl.video_recording:
            v_elapsed = fmt_duration(max(0.0, time.monotonic() - ctrl.video_started_at))
            self.video_status_badge.setText("保存中…" if ctrl.video_stopping else f"● {v_elapsed}")
            ui.repolish(self.video_status_badge, badge="danger")
        else:
            self.video_status_badge.setText("待机")
            ui.repolish(self.video_status_badge, badge="neutral")

    def _sync_ui(self):
        ctrl = self.ctx.log_capture
        has_dev = self.device is not None
        has_buf = bool(ctrl.selected_buffers())

        # 日志抓取按钮与缓冲区锁
        log_active = ctrl.log_recording
        self.capture_btn.setText("停止抓取" if log_active else "开始抓取")
        ui.repolish(self.capture_btn, variant="danger" if log_active else "primary")
        set_icon(
            self.capture_btn,
            "square" if log_active else "circle",
            "on-danger" if log_active else "on-signal",
            hover="on-danger" if log_active else "on-signal",
        )
        self.capture_btn.setEnabled(log_active and not ctrl.log_stopping or (not log_active and has_dev and has_buf))
        self.dump_btn.setEnabled(has_dev and has_buf)
        self.clear_btn.setEnabled(has_dev and has_buf)
        for btn in self.buf_buttons.values():
            btn.setEnabled(not log_active)
        self.select_all_btn.setEnabled(not log_active)
        for btn in self.size_buttons.values():
            btn.setEnabled(has_dev and has_buf)
        self.refresh_g_btn.setEnabled(has_dev)

        # 最近一次抓取
        if ctrl.last_log is not None:
            rec = ctrl.last_log
            self.last_log_badge.setText(f"✓ {buffer_summary(rec.buffers)} · {fmt_size(rec.size_bytes)} · {rec.finished_at}")
            ui.repolish(self.last_log_badge, badge="ok")
            self.last_log_path.setText(rec.path)
            self.copy_log_btn.setEnabled(True)
        else:
            self.last_log_badge.setText("暂无记录")
            ui.repolish(self.last_log_badge, badge="neutral")
            self.last_log_path.clear()
            self.copy_log_btn.setEnabled(False)

        if log_active and ctrl.log_path:
            self.active_log_hint.setText(f"● 正在写入：{Path(ctrl.log_path).name}")
            self.active_log_hint.show()
        else:
            self.active_log_hint.hide()

        # 屏幕录制扩展卡片
        v_active = ctrl.video_recording
        self.video_btn.setText("停止录屏" if v_active else "开始录屏")
        ui.repolish(self.video_btn, variant="danger" if v_active else "secondary")
        set_icon(
            self.video_btn,
            "square" if v_active else "video",
            "on-danger" if v_active else "danger",
            hover="on-danger" if v_active else "danger",
        )
        self.video_btn.setEnabled((v_active and not ctrl.video_stopping) or (not v_active and has_dev))
        for w in (self.sw_bugreport, self.bitrate_combo, self.display_combo):
            w.setEnabled(not v_active)
        self.refresh_displays_btn.setEnabled(has_dev and not v_active)

        if ctrl.last_video is not None:
            vrec = ctrl.last_video
            self.last_video_badge.setText(f"✓ {fmt_size(vrec.size_bytes)} · {vrec.finished_at}")
            ui.repolish(self.last_video_badge, badge="ok")
            self.last_video_path.setText(vrec.path)
            self.copy_video_btn.setEnabled(True)
        else:
            self.last_video_badge.setText("暂无记录")
            ui.repolish(self.last_video_badge, badge="neutral")
            self.last_video_path.clear()
            self.copy_video_btn.setEnabled(False)

        if v_active and ctrl.video_path:
            self.active_video_hint.setText(f"● 正在录制：{Path(ctrl.video_path).name}（停止后自动拉取到本机）")
            self.active_video_hint.show()
        else:
            self.active_video_hint.hide()

        self._sync_live_labels()
        self._sync_timer()
        self.ctx.window.update_status(self)
