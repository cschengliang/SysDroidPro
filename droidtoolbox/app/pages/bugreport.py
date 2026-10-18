"""崩溃与 Bug 报告：浏览 tombstone / ANR / dropbox 记录并预览，生成完整 bugreport。"""
from __future__ import annotations

import time
from pathlib import Path

import tbui as ui
from tbui._qt import QtWidgets, Qt

from ..backend.models import CrashFile
from ..ui.common import BusyBar, Column, DataTable, PanelHead, TaskStrip, TextViewer, fmt_ago, fmt_size, grow, reveal, tbtn
from .base import Page

W = QtWidgets
KIND_TEXT = {"tombstone": ("Tombstone", "danger"), "anr": ("ANR", "warn"), "dropbox": ("Dropbox", "info")}


class BugreportPage(Page):
    id = "bugreport"
    title = "崩溃与 Bug 报告"
    icon = "bug"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        refresh = tb.add(ui.icon_button("refresh-cw", "刷新列表"))
        tb.separator()
        self.kind = tb.add(ui.SegmentedControl([("all", "All"), ("tombstone", "Tombstone"), ("anr", "ANR"), ("dropbox", "Dropbox")],
                                               "all", label="类型"))
        self.filter = tb.add(grow(ui.FilterField("Filter: process / summary", width=260), 260))
        folder = tb.add(ui.icon_button("folder-open", "打开导出目录"))
        gen = tb.add(tbtn("Bugreport", "生成完整 bugreport（adb bugreport，约 1–3 分钟）", "primary", icon="file-text"))
        lay.addWidget(tb)
        self.busy = BusyBar()
        lay.addWidget(self.busy)
        self.strip = TaskStrip((12, 8, 12, 8))
        lay.addWidget(self.strip)

        split = W.QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        left = W.QWidget()
        ll = W.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        self.head = PanelHead("崩溃记录")
        ll.addWidget(self.head)
        self.table = DataTable([
            Column("类型", lambda c: KIND_TEXT[c.kind][0], 100, tone=lambda c: KIND_TEXT[c.kind][1]),
            Column("进程", lambda c: c.process, 170, mono=True, tip=lambda c: c.path),
            Column("时间", lambda c: fmt_ago(c.mtime), 84, sort=lambda c: c.mtime or 0,
                   tip=lambda c: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(c.mtime)) if c.mtime else ""),
            Column("大小", lambda c: fmt_size(c.size), 84, mono=True, align="right", sort=lambda c: c.size or 0),
            Column("摘要", lambda c: c.summary, None, tip=lambda c: c.summary or ""),
        ])
        self.table.sortByColumn(2, Qt.SortOrder.DescendingOrder)
        ll.addWidget(self.table, 1)
        split.addWidget(left)

        right = W.QWidget()
        rl = W.QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.save = ui.icon_button("save", "保存到本地（导出目录 / crash）")
        self.save.setEnabled(False)
        self.preview_head = PanelHead("预览", self.save)
        rl.addWidget(self.preview_head)
        self.viewer = TextViewer("选择左侧的一条记录查看内容")
        rl.addWidget(self.viewer, 1)
        split.addWidget(right)
        split.setSizes([640, 520])
        lay.addWidget(split, 1)

        refresh.clicked.connect(self.refresh)
        self.kind.changed.connect(self._apply_filter)
        self.filter.textChanged.connect(self._apply_filter)
        self.table.selectionChangedRows.connect(self._selected)
        self.save.clicked.connect(self._save)
        folder.clicked.connect(lambda: reveal(str(self.ctx.settings.export_dir())))
        gen.clicked.connect(self._bugreport)
        self._current: CrashFile | None = None
        self._token = 0

    # 生命周期 --------------------------------------------------------
    def load(self, device):
        self.table.set_rows([])
        self.viewer.set_text("")
        self.busy.push()
        self.ctx.run(self.ctx.backend.list_crash_files, device.serial, done=self._got, error=self._failed)

    def unload(self):
        self.table.set_rows([])
        self.viewer.set_text("")
        self._current = None

    def _got(self, rows: list[CrashFile]):
        self.busy.pop()
        self.table.set_rows(rows, keep=lambda c: c.path)
        self._apply_filter()

    def _failed(self, e):
        self.busy.pop()
        self.ctx.report(e, "无法读取崩溃记录")

    def _apply_filter(self, *_):
        kind = self.kind.value()
        self.table.filter.set_predicate(None if kind == "all" else (lambda c: c.kind == kind))
        self.table.filter.set_text(self.filter.text())
        n, total = self.table.filter.rowCount(), self.table.table_model.rowCount()
        self.head.set_count(f"{n} / {total}" if n != total else total)
        self.filter.set_count(n, total)

    # 预览 ------------------------------------------------------------
    def _selected(self, rows):
        c = rows[0] if rows else None
        self._current = c
        self.save.setEnabled(c is not None)
        if c is None:
            return
        self.preview_head.title.setText(c.name)
        self.preview_head.set_count(fmt_size(c.size))
        self.viewer.set_text("正在读取…")
        self._token += 1
        tok = self._token
        self.ctx.run(self.ctx.backend.read_text, self.serial, c.path,
                     done=lambda text: tok == self._token and self.viewer.set_text(text),
                     error=lambda e: tok == self._token and self.viewer.set_text(f"读取失败：{e}"))

    def _save(self):
        c = self._current
        if c is None:
            return
        dest = self.ctx.settings.export_dir() / "crash"
        dest.mkdir(parents=True, exist_ok=True)
        if c.kind == "dropbox":  # dropbox 条目不是普通文件，保存预览文本
            p = dest / (c.name.replace("@", "_") + ".txt")
            p.write_text(self.viewer.plain(), "utf-8")
            self.ctx.notify("ok", "已保存", str(p))
            return
        self.strip.run(f"正在拉取 {c.name}", self.ctx.backend.pull, self.serial, [c.path], str(dest),
                       done=lambda paths: self.ctx.notify("ok", "已保存", paths[0] if paths else str(dest)))

    # Bug 报告 --------------------------------------------------------
    def _bugreport(self):
        if self.strip.busy:
            return
        dest = str(self.ctx.settings.export_dir())
        self.strip.run("正在生成 Bug 报告（通常需要 1–3 分钟）", self.ctx.backend.bugreport, self.serial, dest,
                       done=lambda path: (self.ctx.notify("ok", "Bug 报告已生成", path), reveal(path) if Path(path).exists() else None))
