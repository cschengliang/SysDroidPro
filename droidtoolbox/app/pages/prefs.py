"""偏好设置（工具箱自身的配置，区别于“系统设置”页的 Android settings）：工具路径、外观、行为、导出目录、关于。修改即保存。"""
from __future__ import annotations

import platform
import sys

import tbui as ui
from tbui._qt import BINDING, QtGui, QtWidgets, Qt

from .. import __version__
from ..backend.models import ToolInfo
from ..ui.common import hbox, label, narrow_select, open_files, pick_dir, reveal, tbtn
from .base import Page

W = QtWidgets


class PrefsPage(Page):
    id = "prefs"
    title = "偏好设置"
    icon = "sliders"
    requires = ()

    def build(self):
        outer = W.QVBoxLayout(self.body)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        page = W.QWidget()
        page.setProperty("page", True)
        scroll.setWidget(page)
        v = W.QVBoxLayout(page)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)
        s = self.ctx.settings

        # 工具 --------------------------------------------------------
        tools = W.QWidget()
        tl = W.QVBoxLayout(tools)
        tl.setContentsMargins(16, 16, 16, 16)
        tl.setSpacing(12)
        self.fields = {}
        for key, name, hint in (("adb_path", "adb", "留空则依次查找：附带的 tool/platform-tools → PATH → ANDROID_HOME / ANDROID_SDK_ROOT → Android Studio 默认 SDK；填写了但文件不存在视为未找到"),
                                ("fastboot_path", "fastboot", "留空则依次查找：adb 同目录 → 附带的 tool/platform-tools → PATH → SDK"),
                                ("scrcpy_path", "scrcpy", "用于屏幕投射；留空则依次查找：附带的 tool/scrcpy-win64-v4.1 → PATH")):
            f = ui.TextField(f"{name} 路径", f"自动（{name}.exe）", hint, mono=True)
            f.edit.setText(s.get(key))
            f.edit.editingFinished.connect(lambda k=key, f=f: self._save_path(k, f))
            b = tbtn("Browse", f"选择 {name}.exe", icon="folder-open")
            b.clicked.connect(lambda _=False, k=key, f=f, n=name: self._browse(k, f, n))
            row = hbox(f, b)
            row.layout().setAlignment(b, Qt.AlignmentFlag.AlignVCenter)
            tl.addWidget(row)
            self.fields[key] = f
        detect = tbtn("Detect", "检测 adb / fastboot / scrcpy 路径与版本", icon="refresh-cw")
        restart = tbtn("Restart adb", "adb kill-server + start-server", icon="rotate-ccw")
        detect.clicked.connect(self._detect)
        restart.clicked.connect(lambda: self.ctx.run(self.ctx.backend.restart_server, done=lambda _: (
            self.ctx.notify("ok", "adb 服务已重启"), self.ctx.state.refresh_devices(), self._detect())))
        tl.addWidget(hbox(detect, restart, None))
        self.tool_box = W.QVBoxLayout()
        tl.addLayout(self.tool_box)
        v.addWidget(ui.card("工具链", tools))

        # 外观与行为 ----------------------------------------------------
        beh = W.QWidget()
        form = W.QFormLayout(beh)
        form.setContentsMargins(16, 16, 16, 16)
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        form.setFieldGrowthPolicy(W.QFormLayout.FieldGrowthPolicy.FieldsStayAtSizeHint)
        self.theme_seg = ui.SegmentedControl([("light", "Light"), ("dark", "Dark")], ui.theme().name, label="主题")
        self.theme_seg.changed.connect(self._theme)
        ui.theme().changed.connect(lambda name: self.theme_seg.value() != name and self.theme_seg.set_value(name))
        self._row(form, "主题", self.theme_seg, "Ctrl+T 随时切换")
        auto_root = ui.Switch("On", s.get("auto_root"))
        auto_root.toggled.connect(lambda on: s.set("auto_root", on))
        self._row(form, "连接后自动 adb root", auto_root, "userdebug / eng 固件有效，user 固件自动跳过")
        self._row(form, "设备轮询间隔", self._select("poll_interval_ms", [(1000, "1s"), (2000, "2s"), (5000, "5s")]))
        self._row(form, "Logcat 行数上限", self._select("logcat_max_rows", [(50_000, "50k"), (200_000, "200k"), (500_000, "500k")]),
                  "超过上限丢弃最早的行；重新打开 Logcat 生效")
        self._row(form, "Logcat 启动时回溯", self._select("logcat_tail", [(0, "0"), (500, "500"), (2000, "2000"),
                                                                         (10000, "10k")]), "启动时回溯的行数（logcat -T）")
        ports = hbox(self._port("winscope_web_port", "web"), self._port("winscope_proxy_port", "proxy"), spacing=6)
        self._row(form, "Winscope 端口", ports, "Web 界面 / ADB 代理；修改后下次启动生效")
        tp = self._port("perfetto_rpc_port", "Trace Processor")
        tp.setToolTip("Trace Processor 端口（1024–65535）。Perfetto UI 默认只连接 9001；改成其它端口后，"
                      "需要在 Perfetto 的 Flags 页打开 “Relax Content Security Policy for 127.0.0.1:*”")
        ports = hbox(self._port("perfetto_web_port", "web"), tp, spacing=6)
        self._row(form, "Perfetto 端口", ports, "Web 界面 / Trace Processor（UI 默认连 9001）")
        v.addWidget(ui.card("外观与行为", beh))

        # 导出目录 ------------------------------------------------------
        exp = W.QWidget()
        el = W.QVBoxLayout(exp)
        el.setContentsMargins(16, 16, 16, 16)
        self.export = ui.TextField("导出目录", "", "截图、录屏、日志、Bug 报告、导出的 APK、批量 dump 都保存在这里", mono=True)
        self.export.edit.setText(s.get("export_dir"))
        self.export.edit.editingFinished.connect(lambda: s.set("export_dir", self.export.edit.text().strip() or s.get("export_dir")))
        browse = tbtn("Browse", "选择导出目录", icon="folder-open")
        browse.clicked.connect(self._pick_export)
        open_btn = tbtn("Open", "在资源管理器中打开", icon="external-link")
        open_btn.clicked.connect(lambda: reveal(str(s.export_dir())))
        el.addWidget(hbox(self.export, browse, open_btn))
        v.addWidget(ui.card("文件", exp))

        # 关于 ----------------------------------------------------------
        v.addWidget(ui.card("关于", ui.PropertyList([
            ("版本", __version__), ("后端", self.ctx.backend.name), ("Qt 绑定", BINDING),
            ("Python", f"{platform.python_version()} ({'embedded' if 'embed' in sys.executable.lower() else sys.executable})"),
            ("设置文件", s.file_name),
        ])))
        v.addStretch(1)

    def shown(self):
        self._detect()

    @staticmethod
    def _row(form, text, w, hint=""):
        l = label(text)
        ui.repolish(l, formlabel=True)
        if hint:
            w = hbox(w, label(hint, "faint"), None, spacing=12)
        form.addRow(l, w)

    def _select(self, key, options):
        c = narrow_select([(str(v), t) for v, t in options], str(self.ctx.settings.get(key)), 88)
        c.currentIndexChanged.connect(lambda *_: self.ctx.settings.set(key, int(c.currentData())))
        return c

    def _port(self, key, tip):
        e = W.QLineEdit(str(self.ctx.settings.get(key)))
        e.setFixedWidth(64)
        e.setFont(ui.theme().mono_font(12))
        e.setToolTip(f"{tip} 端口（1024–65535）")
        e.setValidator(QtGui.QIntValidator(1024, 65535, e))
        ui.repolish(e, size="sm")
        e.editingFinished.connect(lambda: e.hasAcceptableInput() and self.ctx.settings.set(key, int(e.text())))
        return e

    def _theme(self, name):
        if name != ui.theme().name:
            ui.theme().set_theme(name)
        self.ctx.settings.set("theme", name)

    def _save_path(self, key, f):
        val = f.edit.text().strip()
        if val != self.ctx.settings.get(key):
            self.ctx.settings.set(key, val)
            self._detect()

    def _browse(self, key, f, name):
        paths = open_files(self, f"选择 {name}.exe", f"{name} (*.exe);;所有文件 (*)")
        if paths:
            f.edit.setText(paths[0])
            self._save_path(key, f)

    def _pick_export(self):
        d = pick_dir(self, "选择导出目录", self.ctx.settings.get("export_dir"))
        if d:
            self.export.edit.setText(d)
            self.ctx.settings.set("export_dir", d)

    def _detect(self):
        self.ctx.run(self.ctx.backend.tool_info, done=self._show_tools)

    def _show_tools(self, info: ToolInfo):
        while self.tool_box.count():
            w = self.tool_box.takeAt(0).widget()
            if w:
                w.deleteLater()

        def row(name, path, ver):
            return (name, f"{ver}  ·  {path}" if path else "未找到")
        self.tool_box.addWidget(ui.PropertyList([row("adb", info.adb_path, info.adb_version),
                                                 row("fastboot", info.fastboot_path, info.fastboot_version),
                                                 row("scrcpy", info.scrcpy_path, info.scrcpy_version),
                                                 ("adb 服务", "运行中" if info.server_running else "未运行")]))
        self.ctx.window.set_tool_info(info)
