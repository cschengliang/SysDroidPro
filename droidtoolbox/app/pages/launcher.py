"""功能启动：在电脑上启动 / 停止辅助服务（目前为 Winscope），查看运行输出。不需要连接设备。"""
from __future__ import annotations

from pathlib import Path

import tbui as ui
from tbui._qt import QtCore, QtGui, QtWidgets

from ..core.host_services import HostService, WinscopeService
from ..core.settings import ROOT
from ..ui.common import Column, DataTable, PanelHead, label, output_log, tbtn
from .base import Page

W = QtWidgets
STATE = {"stopped": ("stopped", "ink-muted"), "starting": ("starting…", "info"), "running": ("running", "ok"),
         "stopping": ("stopping…", "info"), "error": ("error", "danger")}


class LauncherPage(Page):
    id = "launcher"
    title = "功能启动"
    icon = "zap"
    requires = ()

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        self.start_btn = tb.add(tbtn("Start", "启动所选功能", "primary", icon="play"))
        self.stop_btn = tb.add(tbtn("Stop", "停止所选功能", icon="square"))
        tb.separator()
        self.open_btn = tb.add(ui.icon_button("external-link", "在浏览器中打开（双击运行中的行也可以）"))
        self.token_btn = tb.add(ui.icon_button("copy", "复制 Winscope 代理令牌（网页连接 ADB 代理时需要填写）"))
        tb.stretch()
        self.hint = tb.add(label("", "caption"))
        lay.addWidget(tb)

        self.table = DataTable([
            Column("功能", lambda s: s.title, 140),
            Column("状态", lambda s: STATE[s.state][0], 96, mono=True, tone=lambda s: STATE[s.state][1],
                   tip=lambda s: s.error or ""),
            Column("地址", lambda s: s.address() if s.state == "running" else "—", 200, mono=True),
            Column("端口", lambda s: s.detail(), 190, mono=True, tone=lambda s: "ink-muted"),
            Column("备注", lambda s: s.error if s.state == "error" else s.remark, None,
                   tone=lambda s: "danger" if s.state == "error" else "ink-muted", tip=lambda s: s.error or s.remark),
        ], sortable=False)
        lay.addWidget(self.table)

        clear = ui.icon_button("eraser", "清空输出")
        lay.addWidget(PanelHead("输出", clear))
        self.log = output_log(160)
        self.log.write("在上方选择功能后点 Start；Winscope 启动后点打开按钮在浏览器中使用。", "system")
        lay.addWidget(self.log, 1)

        self._adb_dir: str | None = None
        self.services: list[HostService] = [WinscopeService(self.ctx.settings, self._adb_dir_for_proxy)]
        for s in self.services:
            s.stateChanged.connect(lambda st, s=s: self._state_changed(s, st))
            s.output.connect(self.log.write)
        self.table.set_rows(self.services)
        self.table.setFixedHeight(28 + 28 * max(3, len(self.services)) + 2)   # 表头 + 行，不留大片空白
        self.table.selectRow(0)

        self.start_btn.clicked.connect(lambda: self._cur() and self._cur().start())
        self.stop_btn.clicked.connect(lambda: self._cur() and self._cur().stop())
        self.open_btn.clicked.connect(self._open)
        self.token_btn.clicked.connect(self._copy_token)
        self.table.rowActivated.connect(lambda s: self._open() if s.state == "running" else s.start())
        self.table.selectionChangedRows.connect(lambda *_: self._sync_buttons())
        clear.clicked.connect(self.log.out.clear)
        self._sync_buttons()

    # 数据 ------------------------------------------------------------
    def shown(self):
        self.ctx.run(self.ctx.backend.tool_info, done=self._got_tools, error=lambda e: None)

    def _got_tools(self, info):
        if info.adb_path and Path(info.adb_path).exists():
            self._adb_dir = str(Path(info.adb_path).parent)

    def _adb_dir_for_proxy(self) -> str | None:
        """ADB 代理用的 adb 目录：后端实际使用的 adb，取不到时用附带的 tool/platform-tools。"""
        if self._adb_dir:
            return self._adb_dir
        bundled = ROOT / "tool" / "platform-tools"
        return str(bundled) if (bundled / "adb.exe").exists() else None

    def _cur(self) -> HostService | None:
        return self.table.current_row() or (self.services[0] if len(self.services) == 1 else None)

    def status_text(self) -> str:
        running = [s.title for s in self.services if s.state == "running"]
        return ("running: " + ", ".join(running)) if running else "no service running"

    # 状态 ------------------------------------------------------------
    def _state_changed(self, s: HostService, state: str):
        i = self.services.index(s)
        self.table.table_model.update_row(i, s)
        self._sync_buttons()
        self.ctx.window.update_status(self)
        if state == "running":
            self.ctx.notify("ok", f"{s.title} 已启动", s.address())
        elif state == "error":
            self.ctx.notify("danger", f"{s.title} 启动失败", s.error)

    def _sync_buttons(self):
        s = self._cur()
        st = s.state if s else "stopped"
        self.start_btn.setEnabled(s is not None and st in ("stopped", "error"))
        self.stop_btn.setEnabled(s is not None and st in ("starting", "running"))
        self.open_btn.setEnabled(s is not None and st == "running")
        self.token_btn.setEnabled(isinstance(s, WinscopeService))
        self.hint.setText(f"{s.title}: {STATE[st][0]}" if s else "")

    # 操作 ------------------------------------------------------------
    def _open(self):
        s = self._cur()
        if s and s.state == "running":
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(s.address()))

    def _copy_token(self):
        s = self._cur()
        if not isinstance(s, WinscopeService):
            return
        token = s.read_token()
        if not token:
            self.ctx.notify("info", "还没有令牌", "启动一次 Winscope 后 ADB 代理会生成令牌。")
            return
        QtWidgets.QApplication.clipboard().setText(token)
        self.ctx.notify("ok", "已复制 Winscope 令牌", "在 Winscope 网页里连接 ADB 代理时粘贴。")

    def shutdown(self):
        """主窗口关闭时停止所有正在运行的服务，避免留下孤儿进程。"""
        for s in self.services:
            if s.state in ("starting", "running"):
                s.stop()
