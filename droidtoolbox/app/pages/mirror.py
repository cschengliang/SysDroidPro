"""屏幕投射：scrcpy 在独立的新窗口中打开（不嵌入主窗口），窗口标题为“设备名 · 序列号”；
窗口位置 / 大小可设置并记住；不依赖 scrcpy 的按键面板。设备总览的“投屏”按钮调用 quick_start()。
"""
from __future__ import annotations

import time

import tbui as ui
from tbui._qt import QtGui, QtWidgets

from ..backend.api import ToolNotFound
from ..backend.models import ScrcpyOptions
from ..core.jobs import on_ui
from ..ui.common import label, narrow_select, output_log, set_icon, tbtn
from .base import Page

W = QtWidgets
KEYS = [("KEYCODE_BACK", "返回", "arrow-left"), ("KEYCODE_HOME", "主屏幕", "house"), ("KEYCODE_APP_SWITCH", "最近任务", "square"),
        ("KEYCODE_POWER", "电源", "power"), ("KEYCODE_VOLUME_UP", "音量 +", "volume-2"), ("KEYCODE_VOLUME_DOWN", "音量 −", "volume-1"),
        ("KEYCODE_MENU", "菜单", "ellipsis"), ("KEYCODE_WAKEUP", "唤醒", "zap")]
WIN_KEYS = [("mirror_window_x", "x", "窗口 X 坐标"), ("mirror_window_y", "y", "窗口 Y 坐标"),
            ("mirror_window_width", "w", "窗口宽度"), ("mirror_window_height", "h", "窗口高度")]


class MirrorPage(Page):
    id = "mirror"
    title = "屏幕投射"
    icon = "cast"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        # 第一行：启动 / 状态 / 视频参数 / 按键
        tb = ui.Toolbar()
        self.start = tb.add(tbtn("Start", "在独立的新窗口中打开 scrcpy 投屏", "primary", icon="cast", width=76))
        self.state_lbl = tb.add(label("○ idle", "caption"))
        tb.separator()
        self.max_size = tb.add(narrow_select([("0", "native", "原始分辨率"), ("2560", "2560"), ("1920", "1920"), ("1280", "1280"),
                                              ("1024", "1024")], "1920", 80, "最大尺寸（长边像素，--max-size）"))
        self.bitrate = tb.add(narrow_select([("4", "4M"), ("8", "8M"), ("16", "16M"), ("32", "32M")], "8", 64,
                                            "视频码率（--video-bit-rate）"))
        self.fps = tb.add(narrow_select([("0", "max", "不限"), ("30", "30"), ("60", "60"), ("120", "120")], "60", 64,
                                        "最高帧率（--max-fps）"))
        tb.stretch()
        tb.add(label("Keys", "label"))
        for code, tip, icon in KEYS:
            b = tb.add(ui.icon_button(icon, f"{tip}（{code}，不需要 scrcpy）"))
            b.clicked.connect(lambda _=False, c=code: self._key(c))
        lay.addWidget(tb)
        # 第二行：开关与窗口位置
        tb2 = ui.Toolbar()
        tb2.layout().setSpacing(14)             # 开关之间留出间距，文字不贴下一个开关
        self.sw_awake = tb2.add(ui.Switch("Awake", True))
        self.sw_off = tb2.add(ui.Switch("Screen off", False))
        self.sw_touch = tb2.add(ui.Switch("Touches", False))
        self.sw_top = tb2.add(ui.Switch("On top", False))
        self.sw_audio = tb2.add(ui.Switch("Audio", True))
        self.sw_record = tb2.add(ui.Switch("Record", False))
        for sw, tip in ((self.sw_awake, "投屏时保持唤醒（--stay-awake）"), (self.sw_off, "关闭设备屏幕，仍可控制（--turn-screen-off）"),
                        (self.sw_touch, "显示触摸位置（--show-touches）"), (self.sw_top, "窗口置顶（--always-on-top）"),
                        (self.sw_audio, "转发音频，Android 11+（关闭时 --no-audio）"), (self.sw_record, "同时录制到导出目录 recordings/（--record）")):
            sw.setToolTip(tip)
        tb2.separator()
        tb2.add(label("Window", "label"))
        self.win_fields: dict[str, W.QLineEdit] = {}
        for key, ph, tip in WIN_KEYS:
            e = tb2.add(W.QLineEdit(str(self.ctx.settings.get(key))))
            e.setPlaceholderText(ph)
            e.setToolTip(f"{tip}；留空由 scrcpy 决定，修改后自动记住")
            e.setFixedWidth(56)
            e.setFont(ui.theme().mono_font(12))
            ui.repolish(e, size="sm")
            e.setValidator(QtGui.QIntValidator(-10000, 10000, e))
            e.editingFinished.connect(lambda k=key, e=e: self.ctx.settings.set(k, e.text().strip()))
            self.win_fields[key] = e
        tb2.stretch()
        lay.addWidget(tb2)

        self.banner_box = W.QVBoxLayout()
        self.banner_box.setContentsMargins(12, 8, 12, 0)
        lay.addLayout(self.banner_box)
        self.log = output_log(160)
        lay.addWidget(self.log, 1)

        self.start.clicked.connect(self._toggle)
        self._proc = None
        self._serial: str | None = None
        self._gen = 0
        self._ready = True
        self._title = ""

    def load(self, device):
        self._check_tool()

    def unload(self):
        pass   # 投屏窗口独立于页面：切换设备不关闭；设备断开时 scrcpy 自己会退出

    def shown(self):
        self._check_tool()

    def _check_tool(self):
        self.ctx.run(self.ctx.backend.tool_info, done=self._tool)

    def _tool(self, info):
        while self.banner_box.count():
            w = self.banner_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        if not info.scrcpy_path:
            go = tbtn("Prefs", "打开偏好设置", icon="sliders")
            go.clicked.connect(lambda: self.ctx.open_page("prefs"))
            self.banner_box.addWidget(ui.Banner("warn", "未找到 scrcpy",
                                                "下载 scrcpy（github.com/Genymobile/scrcpy）并解压，在偏好设置中指定 scrcpy.exe 的位置。按键面板不受影响。",
                                                action=go))
        self.start.setEnabled(bool(info.scrcpy_path) or self._proc is not None)

    # 投屏 ------------------------------------------------------------
    def quick_start(self):
        """设备总览的“投屏”按钮：用当前参数直接打开投屏窗口，不切换页面。"""
        d = self.ctx.state.current
        if self._proc is not None:
            self.ctx.notify("info", "已经在投屏", f"{self._serial}：投屏窗口已打开")
            return
        if d is None or not d.is_adb:
            self.ctx.notify("warn", "没有可投屏的设备")
            return
        self._start(d)

    def _toggle(self):
        if self._proc is not None:
            self._stop()
            return
        d = self.ctx.state.current
        if d is not None and d.is_adb:
            self._start(d)

    def _int(self, key: str) -> int | None:
        t = self.win_fields[key].text().strip()
        return int(t) if t.lstrip("-").isdigit() else None

    def _start(self, d):
        rec = None
        if self.sw_record.isChecked():
            p = self.ctx.settings.export_dir() / "recordings"
            p.mkdir(parents=True, exist_ok=True)
            rec = str(p / f"scrcpy_{d.serial.replace(':', '_')}_{time.strftime('%Y%m%d-%H%M%S')}.mp4")
        opts = ScrcpyOptions(int(self.max_size.currentData()) or None, int(self.bitrate.currentData()), int(self.fps.currentData()) or None,
                             self.sw_awake.isChecked(), self.sw_off.isChecked(), self.sw_touch.isChecked(), self.sw_top.isChecked(),
                             rec, self.sw_audio.isChecked(), f"{d.display_name} · {d.serial}",
                             self._int("mirror_window_x"), self._int("mirror_window_y"),
                             self._int("mirror_window_width"), self._int("mirror_window_height"))
        self._gen += 1
        g = self._gen
        out = on_ui(lambda text, stream: g == self._gen and self._out(text, stream))
        ex = on_ui(lambda err: g == self._gen and self._exited(err))
        self.log.out.clear()
        self.log.write(f"scrcpy -s {d.serial} --window-title \"{opts.window_title}\"", "input")
        try:
            self._proc = self.ctx.backend.start_scrcpy(d.serial, opts, out, ex)
        except ToolNotFound as e:
            self.ctx.report(e)
            self._check_tool()
            return
        except Exception as e:  # noqa: BLE001
            self.ctx.report(e, "无法启动 scrcpy")
            return
        self._serial = d.serial
        self._title = opts.window_title
        self._ready = False                       # 收到 scrcpy 的 Renderer / Texture 输出才算窗口已打开
        self._set_running(True)
        self.state_lbl.setText(f"● starting {d.serial}")
        self.ctx.notify("info", "正在启动投屏…", opts.window_title)

    def _stop(self):
        self._gen += 1
        if self._proc is not None:
            p, self._proc = self._proc, None
            p.stop()
        self._set_running(False)
        self.log.write("已结束投屏。", "system")

    def _out(self, text: str, stream: str):
        for line in text.rstrip("\n").split("\n"):
            self.log.write(line, "error" if stream == "stderr" or line.startswith("ERROR") else "output")
            if not self._ready and ("Renderer:" in line or "Texture:" in line):
                self._ready = True
                self.state_lbl.setText(f"● mirroring {self._serial}")
                self.ctx.notify("ok", "投屏窗口已打开", self._title)

    def _exited(self, err):
        if not self._ready and err is None:
            self.ctx.notify("warn", "投屏没有启动成功", "scrcpy 在窗口出现前退出了，详细信息见输出区。")
        self._ready = True
        self._proc = None
        self._set_running(False)
        self.log.write(f"scrcpy 已退出：{err}" if err else "投屏窗口已关闭。", "error" if err else "system")

    def _set_running(self, on: bool):
        self.start.setText("Stop" if on else "Start")
        ui.repolish(self.start, variant="danger" if on else "primary")
        set_icon(self.start, "square" if on else "cast", "on-danger" if on else "on-signal",
                 hover="on-danger" if on else "on-signal")
        self.state_lbl.setText(f"● mirroring {self._serial}" if on else "○ idle")
        ui.repolish(self.state_lbl, tone="ok" if on else None)
        for w in (self.max_size, self.bitrate, self.fps, self.sw_awake, self.sw_off, self.sw_touch, self.sw_top, self.sw_audio,
                  self.sw_record, *self.win_fields.values()):
            w.setEnabled(not on)
        self.start.setEnabled(True)

    def _key(self, code: str):
        if self.device is not None:
            self.ctx.run(self.ctx.backend.send_key, self.serial, code)
