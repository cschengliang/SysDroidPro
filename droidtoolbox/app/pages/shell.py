"""Shell 终端：持久 adb shell 会话（cd / su / export 保持有效），↑↓ 历史，Ctrl+C 中断；
“常用命令”菜单读取 ADB 命令页的命令库（adb shell 类型、无参数占位、非持续运行的命令）。"""
from __future__ import annotations

import tbui as ui
from tbui._qt import QtCore, QtGui, QtWidgets

from ..core.jobs import on_ui
from ..ui.common import tbtn
from .base import Page

W = QtWidgets


class ShellPage(Page):
    id = "shell"
    title = "Shell 终端"
    icon = "terminal"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        self.reconnect = tb.add(ui.icon_button("refresh-cw", "重新连接会话"))
        self.interrupt = tb.add(ui.icon_button("square", "中断当前命令（Ctrl+C）"))
        clear = tb.add(ui.icon_button("eraser", "清屏（clear）"))
        tb.separator()
        self.cmd_menu = W.QMenu(self)
        self.cmd_menu.aboutToShow.connect(self._fill_menu)
        self.cmd_menu.triggered.connect(lambda a: a.data() and self._run_cmd(a.data()))
        tb.add(tbtn("Commands", "常用命令（来自 ADB 命令页的命令库）", icon="list", menu=self.cmd_menu))
        su = tb.add(tbtn("su", "", icon="shield"))
        su.setToolTip("在会话中执行 su 切换到 root（需要已 root 的设备）")
        tb.stretch()
        self.state_label = tb.add(W.QLabel())
        ui.repolish(self.state_label, role="caption")
        lay.addWidget(tb)
        self.term = ui.Terminal("$")
        lay.addWidget(self.term, 1)

        self.reconnect.clicked.connect(self._reopen)
        self.interrupt.clicked.connect(self._interrupt)
        clear.clicked.connect(self.term.out.clear)
        su.clicked.connect(lambda: self._run_cmd("su"))
        self.term.submitted.connect(self._submit)
        sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+C"), self.term.input)
        sc.setContext(QtCore.Qt.ShortcutContext.WidgetShortcut)
        sc.activated.connect(self._interrupt)

        self._session = None
        self._gen = 0
        self._partial = {"stdout": "", "stderr": ""}
        self._flush_timer = QtCore.QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.setInterval(120)
        self._flush_timer.timeout.connect(self._flush_partial)

    # 会话 ------------------------------------------------------------
    def load(self, device):
        prod = device.product or device.model or "device"
        self.term.prompt = f"{prod}:/ $"
        self.term._prompt.setText(self.term.prompt)
        self._open()

    def unload(self):
        self._close()
        self.term.write("设备已断开，会话结束。", "system")
        self._set_state(False)

    def shown(self):
        self.term.input.setFocus()

    def refresh(self):
        self._reopen()

    def _open(self):
        self._gen += 1
        g = self._gen
        d = self.device
        self.term.write(f"正在连接 {d.display_name}（{d.serial}）· adb shell …", "system")
        on_out = on_ui(lambda text, stream: g == self._gen and self._output(text, stream))
        on_exit = on_ui(lambda err: g == self._gen and self._exited(err))
        self.ctx.run(self.ctx.backend.open_shell, d.serial, on_out, on_exit,
                     done=lambda s: self._opened(s, g), error=self._open_failed)

    def _opened(self, session, g):
        if g != self._gen:
            session.stop()
            return
        self._session = session
        self.term.write("已连接。输入 exit 结束会话；Ctrl+C 中断前台命令。", "system")
        self._set_state(True)

    def _open_failed(self, e):
        self.term.write(f"连接失败：{e}", "error")
        self._set_state(False)

    def _close(self):
        self._gen += 1
        if self._session is not None:
            s, self._session = self._session, None
            s.stop()

    def _reopen(self):
        if self.device is None:
            return
        self._close()
        self._open()

    def _exited(self, err):
        self._flush_partial()
        self._session = None
        self.term.write(f"会话异常结束：{err}" if err else "会话已结束。点工具栏的“重新连接”开始新会话。", "error" if err else "system")
        self._set_state(False)

    def _set_state(self, connected: bool):
        self.state_label.setText("● connected" if connected else "○ disconnected")
        ui.repolish(self.state_label, tone="ok" if connected else None, role="caption")
        self.term.input.setEnabled(connected)
        self.interrupt.setEnabled(connected)

    # 输入输出 --------------------------------------------------------
    def _submit(self, cmd: str):
        if cmd.strip() == "clear":
            self.term.out.clear()
            return
        if self._session is None:
            self.term.write("会话未连接。", "error")
            return
        self._session.send(cmd)

    def _fill_menu(self):
        """按命令库分组生成子菜单；多行命令逐行发送到会话。"""
        m = self.cmd_menu
        m.clear()
        t = ui.theme()
        usable = [c for c in self.ctx.commands.all() if c.kind == "shell" and not c.persistent and not c.placeholders]
        for g in self.ctx.commands.groups():
            cmds = [c for c in usable if c.group == g]
            if not cmds:
                continue
            sub = m.addMenu(t.icon("list"), g)
            for c in cmds:
                a = sub.addAction(c.name)
                a.setData("\n".join(c.lines()))
                a.setToolTip(c.script)
        m.addSeparator()
        a = m.addAction(t.icon("pencil"), "管理命令库…")
        a.setData("")
        a.triggered.connect(lambda: self.ctx.open_page("commands"))

    def _run_cmd(self, cmd: str):
        if "\n" in cmd:
            for line in cmd.splitlines():
                self._run_cmd(line)
            return
        if self._session is None:
            return
        self.term.write(cmd, "input")
        self.term._hist.append(cmd)
        self._session.send(cmd)
        self.term.input.setFocus()

    def _interrupt(self):
        if self._session is not None:
            self._session.interrupt()

    def _output(self, text: str, stream: str):
        """按行写入；不完整的尾行先缓存，120ms 内没有后续就直接输出（比如交互提示）。"""
        buf = self._partial[stream] + text.replace("\r\n", "\n")
        *lines, rest = buf.split("\n")
        for line in lines:
            self.term.write(line, "error" if stream == "stderr" else "output")
        self._partial[stream] = rest
        if rest:
            self._flush_timer.start()

    def _flush_partial(self):
        for stream, rest in self._partial.items():
            if rest:
                self.term.write(rest, "error" if stream == "stderr" else "output")
                self._partial[stream] = ""
