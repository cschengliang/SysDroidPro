"""Logcat：实时日志、文字 / 正则过滤、最低级别、dmesg、跟随、暂停、导出。

同一台设备重启后重新连接时保留旧日志并插入分隔行（重启前的日志往往最有用）；切换设备时清空。
"""
from __future__ import annotations

import time
from pathlib import Path

import tbui as ui
from tbui._qt import QtCore, QtWidgets

from ..core.jobs import on_ui
from ..ui.common import narrow_select, set_icon, tbtn
from .base import Page

W = QtWidgets
# (值, 显示文字, 说明)：工具栏控件用英文 / 缩写，中文说明放在 tooltip
BUFFERS = [("default", "default", "默认：main + system + crash"), ("main", "main", "应用与大部分系统组件"),
           ("system", "system", "系统服务"), ("crash", "crash", "崩溃堆栈"), ("events", "events", "事件日志（含 am_*、avc）"),
           ("radio", "radio", "通信 / 基带"), ("kernel", "kernel", "内核日志缓冲区（需 root）"), ("all", "all", "全部缓冲区"),
           ("dmesg", "dmesg", "内核环形缓冲区 dmesg -w（需 root）")]
LEVELS = [("V", "V", "Verbose"), ("D", "D", "Debug"), ("I", "I", "Info"), ("W", "W", "Warn"), ("E", "E", "Error"), ("F", "F", "Fatal")]


BUFFER_SETS = {"default": ["main", "system", "crash"], "all": ["all"]}


def _row_text(r: ui.LogRow) -> str:
    return f"{r.time} {r.pid:5d} {r.tid:5d} {r.level} {r.tag}: {r.message}"


class LogcatPage(Page):
    id = "logcat"
    title = "Logcat 日志"
    icon = "scroll-text"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        self.pause = tb.add(ui.icon_button("pause", "暂停显示（继续在后台接收）", checkable=True))
        clear_menu = ui.menu([("view", "清空视图", "eraser"), ("device", "清空设备日志缓冲区（logcat -c）", "trash-2", None, True)], self)
        clear_menu.triggered.connect(lambda a: self._clear(a.data()))
        clear = tb.add(ui.icon_button("eraser", "清空"))
        clear.setMenu(clear_menu)
        clear.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        tb.separator()
        # 过滤框占满工具栏剩余宽度；正则开关单独放成显眼的按钮（与过滤框内的 .* 开关同步）
        self.filter = tb.add(ui.FilterField("Filter: tag / keyword", width=320))
        self.filter.setToolTip("过滤 tag 与消息；打开 Regex 后按正则匹配（如 ^Ok|avc: denied）")
        self.filter.setMinimumWidth(320)
        self.filter.setMaximumWidth(16777215)
        self.filter.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Fixed)
        self._regex_toggle = self.filter._toggles["regex"]
        self._regex_toggle.hide()
        self.regex = tb.add(tbtn("Regex", "正则匹配 tag 与消息", checkable=True, width=64))
        self.level = tb.add(narrow_select(LEVELS, "V", 58, "最低日志级别：V Verbose · D Debug · I Info · W Warn · E Error · F Fatal"))
        tb.separator()
        self.follow = tb.add(ui.icon_button("arrow-down-to-line", "跟随最新", checkable=True))
        self.buffers = tb.add(narrow_select(BUFFERS, "default", 100, "日志缓冲区"))
        self.restart_btn = tb.add(ui.icon_button("refresh-cw", "重新连接"))
        export = tb.add(ui.icon_button("download", "导出日志（已应用过滤时只导出显示的行）"))
        self.follow.setChecked(True)
        lay.addWidget(tb)

        self.banner_box = W.QWidget()
        self.banner_lay = W.QVBoxLayout(self.banner_box)
        self.banner_lay.setContentsMargins(12, 8, 12, 0)
        self.banner_box.hide()
        lay.addWidget(self.banner_box)

        self.view = ui.LogView()
        self.view.log_model.max_rows = self.ctx.settings.get("logcat_max_rows")
        lay.addWidget(self.view, 1)

        self.filter.textChanged.connect(self._apply_filter)
        self.filter.optionsChanged.connect(self._apply_filter)
        self.level.currentIndexChanged.connect(self._apply_filter)
        self.regex.toggled.connect(self._regex_toggle.setChecked)
        self._regex_toggle.toggled.connect(self.regex.setChecked)
        self.view.proxy.invalidPattern.connect(self.filter.set_invalid)
        self.follow.toggled.connect(self.view.set_follow)
        self.view.followChanged.connect(self.follow.setChecked)
        self.pause.toggled.connect(self._on_pause)
        self.view.log_model.rowsInserted.connect(self._count)
        self.view.log_model.modelReset.connect(self._count)
        self.view.log_model.rowsRemoved.connect(self._count)
        self.buffers.currentIndexChanged.connect(lambda *_: self._restart())
        self.restart_btn.clicked.connect(self._restart)
        export.clicked.connect(self._export)

        self._stream = None
        self._gen = 0                         # 每次启动 / 停止 +1，丢弃旧流排队中的回调
        self._held: list = []                 # 暂停期间收到的行
        self._last_serial: str | None = None

    # 生命周期 --------------------------------------------------------
    def load(self, device):
        if device.serial == self._last_serial and self.view.log_model.rowCount():
            self._marker(f"——— {time.strftime('%H:%M:%S')} 设备重新连接 ———")
        else:
            self.view.log_model.clear()
        self._last_serial = device.serial
        self._start(tail=self.ctx.settings.get("logcat_tail"))

    def unload(self):
        self._stop()

    def refresh(self):
        self._restart()

    def _start(self, tail: int | None):
        self._hide_banner()
        key = self.buffers.currentData()
        bufs = BUFFER_SETS.get(key, [key])
        self._gen += 1
        g = self._gen
        on_lines = on_ui(lambda ls: g == self._gen and self._lines(ls))
        on_exit = on_ui(lambda err: g == self._gen and self._exited(err))
        try:
            self._stream = self.ctx.backend.start_logcat(self.serial, bufs, on_lines, on_exit, tail=tail)
        except Exception as e:  # noqa: BLE001
            self._show_banner(str(e))

    def _stop(self):
        self._gen += 1
        if self._stream is not None:
            s, self._stream = self._stream, None
            s.stop()

    def _restart(self):
        if self.device is None:
            return
        self._stop()
        self._marker(f"——— {time.strftime('%H:%M:%S')} 缓冲区：{self.buffers.currentText().replace('缓冲区：', '')} ———")
        # dmesg 是另一份日志，切过去时带上最近的内容；logcat 缓冲区之间切换只接新日志
        self._start(tail=self.ctx.settings.get("logcat_tail") if self.buffers.currentData() == "dmesg" else 0)

    def _exited(self, err):
        self._stream = None
        if err is not None:
            self._show_banner(str(err))

    # 数据 ------------------------------------------------------------
    def _lines(self, lines: list[str]):
        rows = []
        for line in lines:
            if line.startswith("--------- beginning of "):
                continue
            r = ui.LogRow.parse(line)
            if r is None:
                if not line.strip():
                    continue
                r = ui.LogRow("", 0, 0, "V", "", line)   # 无法解析的行原样显示
            rows.append(r)
        if self.pause.isChecked():
            self._held.extend(rows)
            if len(self._held) > self.view.log_model.max_rows:
                del self._held[:len(self._held) - self.view.log_model.max_rows]
            self.pause.setToolTip(f"继续显示（已暂存 {len(self._held):,} 行）")
            return
        self.view.log_model.append_rows(rows)

    def _marker(self, text: str):
        self.view.log_model.append_rows([ui.LogRow(time.strftime("%m-%d %H:%M:%S.000"), 0, 0, "I", "toolbox", text)])

    def _on_pause(self, on: bool):
        set_icon(self.pause, "play" if on else "pause")
        if not on:
            self.view.log_model.append_rows(self._held)
            self._held = []
            self.pause.setToolTip("暂停显示（继续在后台接收）")

    def _clear(self, what: str):
        self.view.log_model.clear()
        self._held = []
        if what == "device" and self.device:
            key = self.buffers.currentData()
            if key == "dmesg":
                self.ctx.notify("info", "dmesg 不支持清空", "只清空了视图。")
                return
            self.ctx.run(self.ctx.backend.clear_logcat, self.serial, BUFFER_SETS.get(key, [key]),
                         done=lambda _: self.ctx.notify("ok", "已清空设备日志缓冲区"))

    def _apply_filter(self, *_):
        o = self.filter.options()
        self.view.set_filter(text=self.filter.text(), min_level=self.level.currentData(), regex=o["regex"],
                             match_case=o["matchCase"], whole_word=o["wholeWord"])
        self._count()

    def _count(self, *_):
        shown, total = self.view.proxy.rowCount(), self.view.log_model.rowCount()
        self.filter.set_count(shown, total)
        self.ctx.window.update_status(self)

    def status_text(self) -> str:
        return f"{self.view.proxy.rowCount():,} / {self.view.log_model.rowCount():,} rows"

    def _export(self):
        rows_all = self.view.log_model.rows
        if not rows_all:
            self.ctx.notify("info", "没有可导出的日志")
            return
        filtered = self.view.proxy.rowCount() != len(rows_all)
        name = f"logcat-{(self.serial or 'device').replace(':', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path, _ = W.QFileDialog.getSaveFileName(self, "导出日志", str(self.ctx.settings.export_dir() / name), "文本文件 (*.txt)")
        if not path:
            return
        if filtered:
            rows = [self.view.proxy.index(i, 0).data(QtCore.Qt.ItemDataRole.UserRole) for i in range(self.view.proxy.rowCount())]
        else:
            rows = list(rows_all)
        Path(path).write_text("\n".join(_row_text(r) for r in rows) + "\n", "utf-8")
        self.ctx.notify("ok", f"已导出 {len(rows):,} 行" + ("（已应用过滤）" if filtered else ""), path)

    # 提示条 ----------------------------------------------------------
    def _show_banner(self, text: str):
        self._hide_banner()
        retry = ui.button("重新连接", size="sm", icon="refresh-cw")
        retry.clicked.connect(self._restart)
        self.banner_lay.addWidget(ui.Banner("danger", "日志流已中断", text, action=retry))
        self.banner_box.show()

    def _hide_banner(self):
        while self.banner_lay.count():
            w = self.banner_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.banner_box.hide()
