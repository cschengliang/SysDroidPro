"""功能启动：在电脑上启动 / 停止辅助服务（Winscope、本地 Perfetto），查看运行输出。不需要连接设备。

每个功能在自己那一行有 Start / Stop / 浏览器打开 / 附加按钮（Winscope 复制令牌、Perfetto 打开 traces 目录）；
工具栏只放“全部停止”。
"""
from __future__ import annotations

from pathlib import Path

import tbui as ui
from tbui._qt import QtCore, QtGui, QtWidgets

from ..core.host_services import HostService, PerfettoService, WinscopeService
from ..core.settings import ROOT
from ..ui.common import Column, DataTable, PanelHead, label, output_log, reveal, tbtn
from .base import Page

W = QtWidgets
STATE = {"stopped": ("stopped", "ink-muted"), "starting": ("starting…", "info"), "running": ("running", "ok"),
         "stopping": ("stopping…", "info"), "error": ("error", "danger")}
ACTIONS_COL = 2
ROW_H = 36                       # 行内放 28px 按钮，比普通表格行（28）高一些


class RowActions(W.QWidget):
    """表格里一行的操作按钮：Start / Stop / 浏览器打开 / 附加按钮（按功能而定，没有则不显示）。"""

    def __init__(self, page: "LauncherPage", svc: HostService):
        super().__init__()
        self.svc = svc
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(6, 0, 6, 0)
        lay.setSpacing(4)
        lay.setAlignment(QtCore.Qt.AlignmentFlag.AlignVCenter)
        self.start_btn = tbtn("Start", f"启动 {svc.title}", "primary", icon="play")
        self.stop_btn = tbtn("Stop", f"停止 {svc.title}", icon="square")
        self.open_btn = ui.icon_button("external-link", "在浏览器中打开（双击运行中的行也可以）")
        if isinstance(svc, WinscopeService):
            self.extra_btn = ui.icon_button("copy", "复制 Winscope 代理令牌（网页连接 ADB 代理时需要填写）")
            self.extra_btn.clicked.connect(lambda: page.copy_token(svc))
        elif isinstance(svc, PerfettoService):
            self.extra_btn = ui.icon_button("folder-open", f"打开 traces 目录（{svc.traces_dir}），trace 文件可以放在这里")
            self.extra_btn.clicked.connect(lambda: reveal(str(svc.traces_dir)))
        else:
            self.extra_btn = ui.icon_button("copy", "")
            self.extra_btn.hide()
        for b in (self.start_btn, self.stop_btn, self.open_btn, self.extra_btn):
            lay.addWidget(b)
        lay.addStretch(1)
        self.start_btn.clicked.connect(svc.start)
        self.stop_btn.clicked.connect(svc.stop)
        self.open_btn.clicked.connect(lambda: page.open_service(svc))
        self.sync()

    def sync(self):
        st = self.svc.state
        self.start_btn.setEnabled(st in ("stopped", "error"))
        self.stop_btn.setEnabled(st in ("starting", "running"))
        self.open_btn.setEnabled(st == "running")


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
        self.stop_all_btn = tb.add(tbtn("Stop all", "停止所有正在运行的功能", icon="square"))
        tb.stretch()
        self.hint = tb.add(label("", "caption"))
        lay.addWidget(tb)

        self.table = DataTable([
            Column("功能", lambda s: s.title, 140),
            Column("状态", lambda s: STATE[s.state][0], 96, mono=True, tone=lambda s: STATE[s.state][1],
                   tip=lambda s: s.error or ""),
            Column("操作", lambda s: "", 240),
            Column("地址", lambda s: s.address() if s.state == "running" else "—", 200, mono=True,
                   tip=lambda s: s.address() if s.state == "running" else ""),
            Column("端口", lambda s: s.detail(), 190, mono=True, tone=lambda s: "ink-muted"),
            Column("备注", lambda s: s.error if s.state == "error" else s.remark, None,
                   tone=lambda s: "danger" if s.state == "error" else "ink-muted", tip=lambda s: s.error or s.remark),
        ], sortable=False)
        lay.addWidget(self.table)

        clear = ui.icon_button("eraser", "清空输出")
        lay.addWidget(PanelHead("输出", clear))
        self.log = output_log(160)
        self.log.write("点功能所在行的 Start 启动，就绪后点打开按钮在浏览器中使用。"
                       "Perfetto 打开 trace 时选 YES（使用本机 Trace Processor）。", "system")
        lay.addWidget(self.log, 1)

        self._adb_dir: str | None = None
        self.services: list[HostService] = [WinscopeService(self.ctx.settings, self._adb_dir_for_proxy),
                                            PerfettoService(self.ctx.settings)]
        self.table.verticalHeader().setDefaultSectionSize(ROW_H)
        self.table.set_rows(self.services)
        self.actions: dict[str, RowActions] = {}
        for i, s in enumerate(self.services):
            self.actions[s.id] = RowActions(self, s)
            self.table.setIndexWidget(self.table.filter.mapFromSource(self.table.table_model.index(i, ACTIONS_COL)),
                                      self.actions[s.id])
            s.stateChanged.connect(lambda st, s=s: self._state_changed(s, st))
            s.output.connect(self.log.write)
        self.table.setFixedHeight(28 + ROW_H * max(2, len(self.services)) + 2)   # 表头 + 行，不留大片空白

        self.stop_all_btn.clicked.connect(self.shutdown)
        self.table.rowActivated.connect(lambda s: self.open_service(s) if s.state == "running" else s.start())
        clear.clicked.connect(self.log.out.clear)
        self._sync_toolbar()

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

    def _running(self) -> list[HostService]:
        return [s for s in self.services if s.state in ("starting", "running")]

    def status_text(self) -> str:
        running = [s.title for s in self.services if s.state == "running"]
        return ("running: " + ", ".join(running)) if running else "no service running"

    # 状态 ------------------------------------------------------------
    def _state_changed(self, s: HostService, state: str):
        self.table.table_model.update_row(self.services.index(s), s)
        self.actions[s.id].sync()
        self._sync_toolbar()
        self.ctx.window.update_status(self)
        if state == "running":
            self.ctx.notify("ok", f"{s.title} 已启动", s.address())
        elif state == "error":
            self.ctx.notify("danger", f"{s.title} 启动失败", s.error)

    def _sync_toolbar(self):
        n = len(self._running())
        self.stop_all_btn.setEnabled(n > 0)
        self.hint.setText(f"{n} / {len(self.services)} running")

    # 操作 ------------------------------------------------------------
    def open_service(self, s: HostService):
        if s.state == "running":
            QtGui.QDesktopServices.openUrl(QtCore.QUrl(s.address()))

    def copy_token(self, s: HostService):
        if not isinstance(s, WinscopeService):
            return
        token = s.read_token()
        if not token:
            self.ctx.notify("info", "还没有令牌", "启动一次 Winscope 后 ADB 代理会生成令牌。")
            return
        QtWidgets.QApplication.clipboard().setText(token)
        self.ctx.notify("ok", "已复制 Winscope 令牌", "在 Winscope 网页里连接 ADB 代理时粘贴。")

    def shutdown(self):
        """停止所有正在运行的服务（“全部停止”按钮；主窗口关闭时也会调用，避免留下孤儿进程）。"""
        for s in self._running():
            s.stop()
