"""系统属性：getprop 全量浏览、前缀 / 文字过滤、setprop、SELinux 上下文列、监视所选属性、快照对比、导出。"""
from __future__ import annotations

import time
from pathlib import Path

import tbui as ui
from tbui._qt import QtCore, QtWidgets, Qt

from ..ui.common import BusyBar, Column, DataTable, Dialog, grow, narrow_select
from ..ui.snapshot import SnapshotBar
from .base import Page

W = QtWidgets
PREFIXES = [("all", "all", "全部属性"), ("ro.", "ro.", "只读属性（build.prop 等）"), ("persist.", "persist.", "持久属性（写入 /data）"),
            ("sys.", "sys.", "系统运行时属性"), ("vendor.", "vendor.", "vendor 属性"), ("init.svc.", "init.svc.", "init 服务状态"),
            ("debug.", "debug.", "调试开关")]
HIGHLIGHT_S = 4          # 监视时变化高亮持续秒数
WATCH_TIP = "监视所选属性：每秒刷新，值变化时高亮（适合观察 init.svc.*、sys.* 状态切换）"


class PropsPage(Page):
    id = "props"
    title = "系统属性"
    icon = "list"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        refresh = tb.add(ui.icon_button("refresh-cw", "重新读取（F5）"))
        tb.separator()
        self.filter = tb.add(grow(ui.FilterField("Filter: name / value", width=260), 180))
        self.prefix = tb.add(narrow_select(PREFIXES, "all", 96, "按前缀筛选"))
        tb.separator()
        self.ctx_btn = tb.add(ui.icon_button("shield", "显示 SELinux 上下文列（getprop -Z）", checkable=True))
        self.watch_btn = tb.add(ui.icon_button("gauge", WATCH_TIP, checkable=True))
        tb.separator()
        self.snap = tb.add(SnapshotBar(self.ctx, "props", lambda s: self.ctx.backend.get_props(s), noun="属性"))
        tb.separator()
        copy = tb.add(ui.icon_button("copy", "复制所选（name=value）"))
        export = tb.add(ui.icon_button("download", "导出为 build.prop 格式"))
        setp = tb.add(ui.icon_button("pencil", "setprop：设置属性（双击一行也可以修改）"))
        lay.addWidget(tb)
        self.busy = BusyBar()
        lay.addWidget(self.busy)
        self.table = DataTable([
            Column("属性", lambda r: r[0], 360, mono=True, tone=self._key_tone),
            Column("值", lambda r: r[1], None, mono=True, tip=lambda r: r[1], tone=self._val_tone),
            Column("SELinux 上下文", lambda r: self._contexts.get(r[0]), 300, mono=True, tone=lambda r: "ink-muted"),
        ], multi=True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setColumnHidden(2, True)
        lay.addWidget(self.table, 1)

        refresh.clicked.connect(self.refresh)
        self.filter.textChanged.connect(self._apply)
        self.prefix.currentIndexChanged.connect(self._apply)
        self.table.rowActivated.connect(lambda r: self._edit(r[0], r[1]))
        self.ctx_btn.toggled.connect(self._toggle_context)
        self.watch_btn.toggled.connect(self._toggle_watch)
        copy.clicked.connect(self._copy)
        export.clicked.connect(self._export)
        setp.clicked.connect(lambda: self._edit(*(self.table.current_row() or ("", ""))[:2]))
        self._props: dict[str, str] = {}
        self._contexts: dict[str, str] = {}
        self._watched: set[str] = set()
        self._changed_at: dict[str, float] = {}
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._poll)
        self._polling = False

    # 颜色：监视中的属性名用 signal-ink，刚变化的值用 warn
    def _key_tone(self, r):
        if r[0] in self._watched:
            return "signal-ink"
        return "ink-muted" if r[0].startswith("ro.") else None

    def _val_tone(self, r):
        return "warn" if time.time() - self._changed_at.get(r[0], 0) < HIGHLIGHT_S else None

    # 加载 ------------------------------------------------------------
    def load(self, device):
        self.snap.set_serial(device.serial)
        self.busy.push()
        self.ctx.run(self.ctx.backend.get_props, device.serial, done=self._got, error=self._failed)
        if self.ctx_btn.isChecked():
            self._load_contexts()

    def unload(self):
        self._props = {}
        self._contexts = {}
        self.watch_btn.setChecked(False)
        self.table.set_rows([])
        self.snap.set_serial("")

    def hidden(self):
        self.watch_btn.setChecked(False)

    def _got(self, props: dict[str, str]):
        self.busy.pop()
        self._props = props
        self.table.set_rows(sorted(props.items()), keep=lambda r: r[0])
        self._apply()

    def _failed(self, e):
        self.busy.pop()
        self.ctx.report(e, "无法读取系统属性")

    def _toggle_context(self, on: bool):
        self.table.setColumnHidden(2, not on)
        if on and not self._contexts and self.device:
            self._load_contexts()

    def _load_contexts(self):
        self.busy.push()

        def done(ctx):
            self.busy.pop()
            self._contexts = ctx
            self.table.viewport().update()
        self.ctx.run(self.ctx.backend.get_prop_contexts, self.serial, done=done, error=self._failed)

    def _apply(self, *_):
        p = self.prefix.currentData()
        self.table.filter.set_predicate(None if p == "all" else (lambda r: r[0].startswith(p)))
        self.table.filter.set_text(self.filter.text())
        self.filter.set_count(self.table.filter.rowCount(), self.table.table_model.rowCount())
        self.ctx.window.update_status(self)

    def status_text(self) -> str:
        w = f" · watch {len(self._watched)}" if self._watched else ""
        return f"{self.table.filter.rowCount():,} / {len(self._props):,} props{w}"

    # 监视 ------------------------------------------------------------
    def _toggle_watch(self, on: bool):
        if on:
            rows = self.table.selected_rows()
            if not rows:
                self.watch_btn.setChecked(False)
                self.ctx.notify("info", "先在表格中选中要监视的属性", "可以按住 Ctrl 多选。")
                return
            self._watched = {r[0] for r in rows}
            self.watch_btn.setToolTip(f"正在监视 {len(self._watched)} 个属性，点击停止")
            self._timer.start()
        else:
            self._timer.stop()
            self._watched = set()
            self.watch_btn.setToolTip(WATCH_TIP)
        self.table.viewport().update()
        self.ctx.window.update_status(self)

    def _poll(self):
        if self._polling or self.device is None:
            return
        self._polling = True

        def done(props):
            self._polling = False
            now = time.time()
            changed = False
            for k in self._watched:
                if props.get(k) != self._props.get(k):
                    self._changed_at[k] = now
                    changed = True
            if changed:
                self._props = props
                self.table.set_rows(sorted(props.items()), keep=lambda r: r[0])
                self._apply()
            else:
                self.table.viewport().update()   # 让高亮按时间淡出

        def fail(e):
            self._polling = False
            self.watch_btn.setChecked(False)
            self.ctx.report(e)
        self.ctx.run(self.ctx.backend.get_props, self.serial, done=done, error=fail)

    # 操作 ------------------------------------------------------------
    def _copy(self):
        rows = self.table.selected_rows()
        if rows:
            QtWidgets.QApplication.clipboard().setText("\n".join(f"{r[0]}={r[1]}" for r in rows))
            self.ctx.notify("ok", f"已复制 {len(rows)} 个属性")

    def _export(self):
        if not self._props:
            return
        name = f"props-{self.serial.replace(':', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.prop"
        path, _ = W.QFileDialog.getSaveFileName(self, "导出属性", str(self.ctx.settings.export_dir() / name), "属性文件 (*.prop *.txt)")
        if path:
            Path(path).write_text("".join(f"{k}={v}\n" for k, v in sorted(self._props.items())), "utf-8")
            self.ctx.notify("ok", f"已导出 {len(self._props)} 个属性", path)

    def _edit(self, key: str = "", value: str = ""):
        d = Dialog(self, "设置属性", "setprop")
        k = d.add(ui.TextField("属性名", "persist.sys.example", mono=True))
        v = d.add(ui.TextField("值", "", "留空表示清空该属性", mono=True))
        k.edit.setText(key)
        v.edit.setText(value)
        d.add_text("<b>ro.*</b> 为只读属性，只能在启动时由 build.prop 设置；<b>persist.*</b> 会写入 /data 并在重启后保留。"
                   "userdebug 上 adb root 后可以设置大多数非 ro 属性。")
        (v.edit if key else k.edit).setFocus()

        def validate():
            name = k.edit.text().strip()
            err = None if name and " " not in name else "属性名不能为空或包含空格"
            if not err and name.startswith("ro.") and name in self._props:
                err = "ro.* 属性已设置后不可修改"
            k.set_error(err)
            return err
        d.validate = validate
        if not d.run():
            return
        key, value = k.edit.text().strip(), v.edit.text()
        self.ctx.run(self.ctx.backend.set_prop, self.serial, key, value,
                     done=lambda _: (self.ctx.notify("ok", "属性已设置", f"{key} = {value}"), self.refresh()))
