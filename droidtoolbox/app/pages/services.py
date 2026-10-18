"""服务与组件：Binder 服务（dumpsys）、init 服务（启停）、HAL（lshal / AIDL）、进程（状态、线程、库、调用栈）。

四个标签按需加载：切到某个标签时才读取，设备变化后重新读取。
"""
from __future__ import annotations

import time
from pathlib import Path

import tbui as ui
from tbui._qt import QtCore, QtWidgets, Qt

from ..backend.models import BinderService, HalInfo, InitService, ProcessDetail, ProcessInfo
from ..ui.common import BusyBar, Column, DataTable, PanelHead, SegmentStack, TextViewer, fmt_size, grow, narrow_select, tbtn
from .base import Page

W = QtWidgets
# 系统工程师常看的服务置顶
PINNED = ["activity", "window", "SurfaceFlinger", "package", "power", "battery", "display", "input", "meminfo",
          "cpuinfo", "gfxinfo", "batterystats", "thermalservice", "media.audio_flinger", "media.camera", "wifi"]
STATE_TONE = {"running": "ok", "stopped": None, "restarting": "warn"}


def _split(left: W.QWidget, right: W.QWidget, sizes=(520, 640)) -> W.QSplitter:
    s = W.QSplitter(Qt.Orientation.Horizontal)
    s.setChildrenCollapsible(False)
    s.addWidget(left)
    s.addWidget(right)
    s.setSizes(list(sizes))
    return s


def _column(*widgets, stretch_last=True) -> W.QWidget:
    w = W.QWidget()
    lay = W.QVBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    for i, x in enumerate(widgets):
        lay.addWidget(x, 1 if (stretch_last and i == len(widgets) - 1) else 0)
    return w


class ServicesPage(Page):
    id = "services"
    title = "服务与组件"
    icon = "server"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self.tabs = SegmentStack([("binder", "Binder", self._build_binder()), ("init", "init", self._build_init()),
                                  ("hal", "HAL", self._build_hal()), ("proc", "Process", self._build_proc())])
        self.tab_refresh = ui.icon_button("refresh-cw", "刷新当前标签（F5）")
        self.tabs.header.add(self.tab_refresh)
        self.tabs.header.stretch()
        self.tab_hint = self.tabs.header.add(W.QLabel())
        ui.repolish(self.tab_hint, role="caption")
        lay.addWidget(self.tabs, 1)
        self.busy = BusyBar()
        self.tabs.layout().insertWidget(1, self.busy)   # 标签栏下方
        self._dirty = {"binder", "init", "hal", "proc"}
        self.tabs.changed.connect(self._tab_changed)
        self.tab_refresh.clicked.connect(self.refresh)
        self._tok = {"binder": 0, "hal": 0, "proc": 0}
        self._tab_changed(self.tabs.value())

    # 生命周期 --------------------------------------------------------
    def load(self, device):
        self._dirty = {"binder", "init", "hal", "proc"}
        self._load(self.tabs.value())

    def unload(self):
        self.init_timer.stop()
        for t in (self.b_table, self.i_table, self.h_table, self.p_table):
            t.set_rows([])
        for v in (self.b_view, self.h_view, self.p_view):
            v.set_text("")

    def hidden(self):
        self.init_auto.setChecked(False)

    def refresh(self):
        self._dirty.add(self.tabs.value())
        self._load(self.tabs.value())

    def _tab_changed(self, key):
        self.tab_hint.setText({"binder": "选中服务执行 dumpsys；“帮助”执行 dumpsys <服务> -h",
                               "init": "启停 init 服务需要 root；push 模块后可在这里重启对应服务",
                               "hal": "HIDL 来自 lshal，AIDL 来自 service list 中的 android.hardware.*",
                               "proc": "选中进程查看 status、线程、已加载的 .so；调用栈需要 root"}[key])
        if key != "init":
            self.init_auto.setChecked(False)
        if self.device is not None and key in self._dirty:
            self._load(key)
        self.ctx.window.update_status(self)

    def _load(self, key):
        if self.device is None:
            return
        self._dirty.discard(key)
        serial, b = self.serial, self.ctx.backend
        fn = {"binder": b.list_binder_services, "init": b.list_init_services, "hal": b.list_hals, "proc": b.list_processes}[key]
        done = {"binder": self._got_binder, "init": self._got_init, "hal": self._got_hal, "proc": self._got_proc}[key]
        self.busy.push()

        def ok(rows):
            self.busy.pop()
            if serial == self.serial:
                done(rows)
                self.ctx.window.update_status(self)

        def fail(e):
            self.busy.pop()
            self.ctx.report(e)
        self.ctx.run(fn, serial, done=ok, error=fail)

    def status_text(self) -> str:
        t = {"binder": self.b_table, "init": self.i_table, "hal": self.h_table, "proc": self.p_table}[self.tabs.value()]
        return f"{t.filter.rowCount()} / {t.table_model.rowCount()}"

    # Binder ----------------------------------------------------------
    def _build_binder(self) -> W.QWidget:
        self.b_filter = ui.FilterField("Filter: service / interface", width=220)
        self.b_head = PanelHead("服务", self.b_filter)
        self.b_table = DataTable([
            Column("服务", lambda s: ("★ " if s.name in PINNED else "") + s.name, 220, mono=True),
            Column("接口", lambda s: s.interface, None, mono=True, tone=lambda s: "ink-muted", tip=lambda s: s.interface or ""),
        ], sortable=False)
        self.b_view = TextViewer("选择左侧的服务查看 dumpsys 输出")
        tb = self.b_view.toolbar
        tb.separator()
        self.b_cmd = tb.add(ui.badge("dumpsys", mono=True))
        self.b_args = tb.add(W.QLineEdit())
        self.b_args.setPlaceholderText("参数，如 -a")
        self.b_args.setFont(ui.theme().mono_font(12))
        self.b_args.setFixedWidth(150)
        ui.repolish(self.b_args, size="sm")
        run = tb.add(tbtn("Run", "执行 dumpsys <服务> <参数>", icon="play", width=60))
        help_ = tb.add(tbtn("-h", "dumpsys <服务> -h：查看该服务支持的参数", width=40))
        save = tb.add(ui.icon_button("save", "保存输出"))
        self.b_table.selectionChangedRows.connect(lambda rows: rows and self._dump(rows[0].name))
        self.b_filter.textChanged.connect(lambda s: self._filter(self.b_table, self.b_filter, s))
        run.clicked.connect(lambda: self._dump(self._b_service))
        self.b_args.returnPressed.connect(lambda: self._dump(self._b_service))
        help_.clicked.connect(lambda: self._dump(self._b_service, ["-h"]))
        save.clicked.connect(lambda: self._save(self.b_view, f"dumpsys-{self._b_service}"))
        self._b_service: str | None = None
        return _split(_column(self.b_head, self.b_table), self.b_view, (520, 640))

    def _got_binder(self, rows: list[BinderService]):
        pinned = [s for n in PINNED for s in rows if s.name == n]
        self.b_table.set_rows(pinned + [s for s in rows if s.name not in PINNED], keep=lambda s: s.name)
        self.b_head.set_count(len(rows))
        self._filter(self.b_table, self.b_filter, self.b_filter.text())

    def _dump(self, service: str | None, args: list[str] | None = None):
        if not service or self.device is None:
            return
        self._b_service = service
        args = args if args is not None else self.b_args.text().split()
        self.b_cmd.setText("dumpsys " + service + (" " + " ".join(args) if args else ""))
        self.b_view.set_text("正在执行…")
        self._tok["binder"] += 1
        tok = self._tok["binder"]
        self.ctx.run(self.ctx.backend.dumpsys, self.serial, service, args,
                     done=lambda text: tok == self._tok["binder"] and self.b_view.set_text(text),
                     error=lambda e: tok == self._tok["binder"] and self.b_view.set_text(f"执行失败：{e}\n{getattr(e, 'detail', '')}"))

    # init ----------------------------------------------------------
    def _build_init(self) -> W.QWidget:
        bar = ui.Toolbar()
        self.i_filter = bar.add(grow(ui.FilterField("Filter: service", width=240), 240))
        self.i_state = bar.add(narrow_select([("all", "all"), ("running", "running"), ("stopped", "stopped"),
                                                    ("restarting", "restarting")], "all", 100, "按状态筛选"))
        bar.separator()
        self.init_auto = bar.add(tbtn("Auto", "", icon="refresh-cw"))
        self.init_auto.setCheckable(True)
        self.init_auto.setToolTip("每 2 秒刷新一次状态")
        bar.separator()
        start = bar.add(tbtn("Start", "start <服务>（需要 root）", icon="play"))
        stop = bar.add(tbtn("Stop", "stop <服务>（需要 root）", icon="square"))
        restart = bar.add(tbtn("Restart", "stop + start（需要 root）", "primary", icon="rotate-ccw"))
        self.i_table = DataTable([
            Column("服务", lambda s: s.name, 360, mono=True),
            Column("状态", lambda s: s.state, 140, mono=True, tone=lambda s: STATE_TONE.get(s.state)),
            Column("属性", lambda s: f"init.svc.{s.name}", None, mono=True, tone=lambda s: "ink-faint"),
        ], multi=True)
        self.i_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.i_filter.textChanged.connect(self._apply_init)
        self.i_state.currentIndexChanged.connect(self._apply_init)
        start.clicked.connect(lambda: self._control("start"))
        stop.clicked.connect(lambda: self._control("stop"))
        restart.clicked.connect(lambda: self._control("restart"))
        self.init_timer = QtCore.QTimer(self)
        self.init_timer.setInterval(2000)
        self.init_timer.timeout.connect(lambda: self._load("init"))
        self.init_auto.toggled.connect(lambda on: self.init_timer.start() if on else self.init_timer.stop())
        return _column(bar, self.i_table)

    def _got_init(self, rows: list[InitService]):
        self.i_table.set_rows(rows, keep=lambda s: s.name)
        self._apply_init()

    def _apply_init(self, *_):
        st = self.i_state.currentData()
        self.i_table.filter.set_predicate(None if st == "all" else (lambda s: s.state == st))
        self._filter(self.i_table, self.i_filter, self.i_filter.text())

    def _control(self, action: str):
        rows = self.i_table.selected_rows()
        if not rows:
            self.ctx.notify("info", "先选中要操作的 init 服务")
            return
        names = [s.name for s in rows]
        verb = {"start": "启动", "stop": "停止", "restart": "重启"}[action]
        critical = [n for n in names if n in ("zygote", "zygote_secondary", "servicemanager", "hwservicemanager", "surfaceflinger", "adbd")]
        body = f"将执行 <code>{'stop + start' if action == 'restart' else action}</code>：<br><code>" + "<br>".join(names) + "</code>"
        if critical:
            body += f"<br><b>{'、'.join(critical)} 是核心服务，{verb}会导致界面重启或 adb 断开。</b>"
        if not ui.confirm(self, f"{verb} {len(names)} 个服务？", body, verb, danger=bool(critical) or action == "stop"):
            return
        b, serial = self.ctx.backend, self.serial

        def work():
            for n in names:
                b.control_init_service(serial, n, action)
        self.ctx.run(work, done=lambda _: (self.ctx.notify("ok", f"已{verb}", "、".join(names)), self._load("init"),
                                           QtCore.QTimer.singleShot(2000, lambda: self._load("init"))))

    # HAL -----------------------------------------------------------
    def _build_hal(self) -> W.QWidget:
        self.h_filter = ui.FilterField("Filter: HAL / process", width=200)
        self.h_kind = ui.SegmentedControl([("all", "All"), ("aidl", "AIDL"), ("hidl", "HIDL")], "all", label="类型")
        self.h_head = PanelHead("HAL", self.h_kind, self.h_filter)
        self.h_table = DataTable([
            Column("名称", lambda h: h.name, 340, mono=True, tip=lambda h: h.name),
            Column("类型", lambda h: h.kind.upper(), 56, tone=lambda h: "info" if h.kind == "aidl" else "special"),
            Column("传输", lambda h: h.transport, 90, mono=True),
            Column("PID", lambda h: h.pid, 64, mono=True, align="right"),
            Column("服务端进程", lambda h: h.process, None, mono=True, tone=lambda h: "ink-muted"),
        ])
        self.h_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.h_view = TextViewer("选择 HAL 后点“调试输出”")
        tb = self.h_view.toolbar
        tb.separator()
        dbg = tb.add(tbtn("Debug", "调试输出：HIDL 为 lshal debug，AIDL 为 dumpsys", icon="bug"))
        goto = tb.add(tbtn("Process", "跳到该 HAL 的服务端进程", icon="cpu"))
        save = tb.add(ui.icon_button("save", "保存输出"))
        self.h_filter.textChanged.connect(self._apply_hal)
        self.h_kind.changed.connect(self._apply_hal)
        dbg.clicked.connect(self._hal_debug)
        self.h_table.rowActivated.connect(lambda h: self._hal_debug())
        goto.clicked.connect(self._hal_process)
        save.clicked.connect(lambda: self._save(self.h_view, "hal-debug"))
        return _split(_column(self.h_head, self.h_table), self.h_view, (700, 480))

    def _got_hal(self, rows: list[HalInfo]):
        self.h_table.set_rows(rows, keep=lambda h: h.name)
        self.h_head.set_count(len(rows))
        self._apply_hal()

    def _apply_hal(self, *_):
        k = self.h_kind.value()
        self.h_table.filter.set_predicate(None if k == "all" else (lambda h: h.kind == k))
        self._filter(self.h_table, self.h_filter, self.h_filter.text())

    def _hal_debug(self):
        h: HalInfo | None = self.h_table.current_row()
        if h is None:
            return
        self.h_view.set_text("正在执行…")
        self._tok["hal"] += 1
        tok = self._tok["hal"]
        self.ctx.run(self.ctx.backend.hal_debug, self.serial, h.name, h.kind,
                     done=lambda t: tok == self._tok["hal"] and self.h_view.set_text(t),
                     error=lambda e: tok == self._tok["hal"] and self.h_view.set_text(f"执行失败：{e}"))

    def _hal_process(self):
        h: HalInfo | None = self.h_table.current_row()
        if h is None or h.pid is None:
            self.ctx.notify("info", "该 HAL 没有独立的服务端进程" if h else "先选中一个 HAL")
            return
        self._pending_pid = h.pid
        loaded = "proc" not in self._dirty
        self.tabs.seg.set_value("proc")
        if loaded:                     # 进程列表已加载：直接定位，否则等加载完成后在 _got_proc 中定位
            self._got_proc(list(self.p_table.table_model.rows))

    # 进程 ----------------------------------------------------------
    def _build_proc(self) -> W.QWidget:
        self.p_filter = ui.FilterField("Filter: name / user / pid", width=220)
        self.p_head = PanelHead("进程", self.p_filter)
        self.p_table = DataTable([
            Column("PID", lambda p: p.pid, 64, mono=True, align="right"),
            Column("PPID", lambda p: p.ppid, 64, mono=True, align="right", tone=lambda p: "ink-muted"),
            Column("用户", lambda p: p.user, 110, mono=True),
            Column("RSS", lambda p: fmt_size(p.rss) if p.rss else "—", 88, mono=True, align="right", sort=lambda p: p.rss or 0),
            Column("名称", lambda p: p.name, None, mono=True),
        ])
        self.p_table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.p_view = TextViewer("选择进程查看详情")
        tb = self.p_view.toolbar
        tb.separator()
        bt = tb.add(tbtn("Backtrace", "", icon="scroll-text"))
        bt.setToolTip("debuggerd -b <pid>（需要 root）")
        kill = tb.add(tbtn("Kill", "结束进程（SIGKILL）", "ghost", icon="x"))
        save = tb.add(ui.icon_button("save", "保存"))
        self.p_filter.textChanged.connect(lambda s: self._filter(self.p_table, self.p_filter, s))
        self.p_table.selectionChangedRows.connect(lambda rows: rows and self._proc_detail(rows[0]))
        bt.clicked.connect(self._backtrace)
        kill.clicked.connect(self._kill)
        save.clicked.connect(lambda: self._save(self.p_view, "process"))
        self._pending_pid: int | None = None
        return _split(_column(self.p_head, self.p_table), self.p_view, (560, 620))

    def _got_proc(self, rows: list[ProcessInfo]):
        self.p_table.set_rows(rows, keep=lambda p: p.pid)
        self.p_head.set_count(len(rows))
        self._filter(self.p_table, self.p_filter, self.p_filter.text())
        if self._pending_pid is not None:
            pid, self._pending_pid = self._pending_pid, None
            for r in range(self.p_table.filter.rowCount()):
                if self.p_table.filter.index(r, 0).data(Qt.ItemDataRole.UserRole) == pid:
                    self.p_table.selectRow(r)
                    self.p_table.scrollTo(self.p_table.filter.index(r, 0))
                    break

    def _proc_detail(self, p: ProcessInfo):
        self.p_view.set_text(f"正在读取 {p.name}（{p.pid}）…")
        self._tok["proc"] += 1
        tok = self._tok["proc"]

        def done(d: ProcessDetail):
            if tok != self._tok["proc"]:
                return
            libs = "\n".join("  " + l for l in d.libraries) or "  （无法读取，可能需要 root）"
            threads = "\n".join(f"  {tid:>6}  {name}" for tid, name in d.threads) or "  （无法读取）"
            self.p_view.set_text(
                f"== 进程 {d.pid} · {d.name}\ncmdline: {d.cmdline}\n打开的文件: {d.fd_count if d.fd_count is not None else '—'}\n\n"
                f"== /proc/{d.pid}/status\n{d.status.rstrip()}\n\n== 线程（{len(d.threads)}）\n{threads}\n\n"
                f"== 已加载的 .so（{len(d.libraries)}）\n{libs}\n")
        self.ctx.run(self.ctx.backend.process_detail, self.serial, p.pid, done=done,
                     error=lambda e: tok == self._tok["proc"] and self.p_view.set_text(f"读取失败：{e}"))

    def _backtrace(self):
        p: ProcessInfo | None = self.p_table.current_row()
        if p is None:
            return
        self.p_view.set_text(f"$ debuggerd -b {p.pid}\n正在抓取调用栈…")
        self._tok["proc"] += 1
        tok = self._tok["proc"]
        self.ctx.run(self.ctx.backend.dump_backtrace, self.serial, p.pid,
                     done=lambda t: tok == self._tok["proc"] and self.p_view.set_text(f"$ debuggerd -b {p.pid}\n\n{t}"),
                     error=lambda e: tok == self._tok["proc"] and self.p_view.set_text(f"$ debuggerd -b {p.pid}\n\n失败：{e}"))

    def _kill(self):
        p: ProcessInfo | None = self.p_table.current_row()
        if p is None:
            return
        if not ui.confirm(self, f"结束进程 {p.pid}？", f"将向 <code>{p.name}</code>（PID {p.pid}，用户 {p.user}）发送 SIGKILL。"
                          "结束系统进程可能导致系统重启或服务被 init 拉起。", "结束进程", danger=True):
            return
        self.ctx.run(self.ctx.backend.kill_process, self.serial, p.pid,
                     done=lambda _: (self.ctx.notify("ok", f"已结束 {p.name}", f"PID {p.pid}"), self.refresh()))

    # 公共 ----------------------------------------------------------
    @staticmethod
    def _filter(table: DataTable, field, text: str):
        table.filter.set_text(text)
        field.set_count(table.filter.rowCount(), table.table_model.rowCount())

    def _save(self, viewer: TextViewer, stem: str):
        if not viewer.plain().strip():
            return
        name = f"{stem.replace('/', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path, _ = W.QFileDialog.getSaveFileName(self, "保存输出", str(self.ctx.settings.export_dir() / name), "文本文件 (*.txt)")
        if path:
            Path(path).write_text(viewer.plain(), "utf-8")
            self.ctx.notify("ok", "已保存", path)
