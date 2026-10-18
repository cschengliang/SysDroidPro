"""ADB 命令：命令库（内置预设 + 我的命令）、编辑保存、参数占位、对当前设备执行、着色结果与执行历史。"""
from __future__ import annotations

import shlex
import time

import tbui as ui
from tbui._qt import QtGui, QtWidgets, Qt

from ..core.commands import Command, HistoryItem
from ..core.jobs import on_ui
from ..ui.common import Dialog, PanelHead, TextViewer, hbox, label, narrow_select, set_icon, tbtn
from .base import Page

W = QtWidgets
ROLE_ID = Qt.ItemDataRole.UserRole + 1
TIMEOUTS = [("10", "10s", "10 秒"), ("30", "30s", "30 秒"), ("60", "60s", "60 秒"), ("120", "2m", "2 分钟"), ("600", "10m", "10 分钟")]


class AdbCommandsPage(Page):
    id = "commands"
    title = "ADB 命令"
    icon = "terminal"
    requires = ()   # 不连接设备也能编辑命令库；执行时再检查设备

    def build(self):
        store = self.ctx.commands
        lay = W.QHBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        split = W.QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        lay.addWidget(split)

        # 左：命令库 --------------------------------------------------
        left = W.QWidget()
        left.setProperty("surface", "panel")
        ll = W.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        new = ui.icon_button("plus", "新建命令")
        io = ui.menu([("import", "导入 JSON…", "upload"), ("export", "导出我的命令…", "download")], self)
        io.triggered.connect(lambda a: self._import() if a.data() == "import" else self._export())
        io_btn = ui.icon_button("ellipsis", "导入 / 导出")
        io_btn.setMenu(io)
        io_btn.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.lib_head = PanelHead("命令库", new, io_btn)
        ll.addWidget(self.lib_head)
        fb = W.QWidget()
        fl = W.QHBoxLayout(fb)
        fl.setContentsMargins(8, 8, 8, 8)
        self.search = ui.FilterField("搜索名称或命令", width=240)
        fl.addWidget(self.search)
        ll.addWidget(fb)
        self.tree = W.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        ui.repolish(self.tree, plain=True)
        ll.addWidget(self.tree, 1)
        split.addWidget(left)

        # 右：编辑 + 结果 ---------------------------------------------
        right = W.QSplitter(Qt.Orientation.Vertical)
        right.setChildrenCollapsible(False)
        split.addWidget(right)
        split.setSizes([280, 900])

        editor = W.QWidget()
        editor.setProperty("page", True)
        ev = W.QVBoxLayout(editor)
        ev.setContentsMargins(16, 10, 16, 10)
        ev.setSpacing(6)
        row1 = W.QHBoxLayout()
        row1.setSpacing(12)
        self.f_name = ui.TextField("名称", "例如：查看当前焦点窗口")
        self.f_group = W.QComboBox()
        self.f_group.setEditable(True)
        self.f_group.setMinimumWidth(160)
        gl = W.QVBoxLayout()
        gl.setSpacing(4)
        glbl = label("分组")
        ui.repolish(glbl, fieldlabel=True)
        gl.addWidget(glbl)
        gl.addWidget(self.f_group)
        gl.addStretch(1)
        self.f_kind = ui.SegmentedControl([("shell", "adb shell"), ("adb", "adb")], "shell", mono=True, label="类型")
        kl = W.QVBoxLayout()
        kl.setSpacing(4)
        klbl = label("类型")
        ui.repolish(klbl, fieldlabel=True)
        kl.addWidget(klbl)
        kl.addWidget(self.f_kind)
        kl.addStretch(1)
        row1.addWidget(self.f_name, 1)
        row1.addLayout(gl)
        row1.addLayout(kl)
        ev.addLayout(row1)
        slbl = label("命令（多行逐行执行；{参数} 执行时填写；# 开头为注释）")
        ui.repolish(slbl, fieldlabel=True)
        ev.addWidget(slbl)
        self.f_script = W.QPlainTextEdit()
        self.f_script.setFont(ui.theme().mono_font(13))
        self.f_script.setMinimumHeight(84)
        self.f_script.setMaximumHeight(160)
        self.f_script.setTabChangesFocus(True)
        ui.repolish(self.f_script, field=True)
        ev.addWidget(self.f_script)
        self.preview = label("", "faint", mono=True, px=11, select=True)
        self.preview.setWordWrap(True)
        ev.addWidget(self.preview)
        opts = W.QHBoxLayout()
        opts.setSpacing(20)
        self.f_confirm = ui.Switch("Confirm")
        self.f_confirm.setToolTip("执行前弹出确认框（危险命令用）")
        self.f_persist = ui.Switch("Persistent")
        self.f_persist.setToolTip("持续运行：不限时，执行中可点 Stop 停止（只执行第一行）")
        self.f_stop = ui.Switch("Stop on error", True)
        self.f_stop.setToolTip("多行命令中某行失败（退出码非 0）就停止")
        self.f_timeout = narrow_select(TIMEOUTS, "30", 64, "单行超时")
        for w in (self.f_confirm, self.f_persist, self.f_stop):
            opts.addWidget(w)
        tl = label("Timeout")
        ui.repolish(tl, formlabel=True)
        opts.addWidget(tl)
        opts.addWidget(self.f_timeout)
        opts.addStretch(1)
        ev.addLayout(opts)
        self.f_desc = ui.TextField("", "说明（可选）")
        ev.addWidget(self.f_desc)

        self.run_btn = tbtn("Run", "对当前设备执行（Ctrl+Enter）", "primary", icon="play", width=72)
        self.save_btn = tbtn("Save", "保存（Ctrl+S）", icon="save", width=68)
        self.copy_btn = tbtn("Duplicate", "复制为我的命令", icon="copy")
        self.del_btn = tbtn("Delete", "删除这条命令", "ghost", icon="trash-2")
        self.ro_badge = ui.badge("built-in · read-only", "info")
        self.ro_badge.setToolTip("内置命令只读：可以直接执行；要修改请点 Duplicate 复制为我的命令")
        self.up_btn = ui.icon_button("chevron-up", "上移")
        self.down_btn = ui.icon_button("chevron-down", "下移")
        self.target = label("", "caption")
        ev.addWidget(hbox(self.run_btn, self.save_btn, self.copy_btn, self.del_btn, self.up_btn, self.down_btn, self.ro_badge, None,
                          self.target, spacing=4))
        right.addWidget(editor)

        res = W.QWidget()
        rl = W.QVBoxLayout(res)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)
        self.status = label("", px=12)
        self.status.setMinimumWidth(260)
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.stop_btn = tbtn("Stop", "停止持续运行的命令", "danger", icon="square", width=64)
        self.stop_btn.hide()
        self.hist = ui.select([("", "History")], size="sm")
        self.hist.setMinimumWidth(240)
        rl.addWidget(PanelHead("执行结果", self.status, self.stop_btn, self.hist))
        self.result = TextViewer("执行结果显示在这里")
        tb = self.result.toolbar
        save_out = tb.add(ui.icon_button("save", "保存结果为 txt"))
        clear = tb.add(ui.icon_button("eraser", "清空结果"))
        rl.addWidget(self.result, 1)
        right.addWidget(res)
        right.setSizes([380, 420])

        # 信号 --------------------------------------------------------
        store.changed.connect(self._fill_tree)
        self.search.textChanged.connect(self._fill_tree)
        self.tree.currentItemChanged.connect(lambda cur, _p: self._select(cur.data(0, ROLE_ID) if cur else None))
        self.tree.itemDoubleClicked.connect(lambda it, _c: it.data(0, ROLE_ID) and self._run())
        new.clicked.connect(self._new)
        self.run_btn.clicked.connect(self._run)
        self.save_btn.clicked.connect(self._save)
        self.copy_btn.clicked.connect(self._duplicate)
        self.del_btn.clicked.connect(self._delete)
        self.up_btn.clicked.connect(lambda: self._cur and store.move(self._cur.id, -1))
        self.down_btn.clicked.connect(lambda: self._cur and store.move(self._cur.id, 1))
        self.stop_btn.clicked.connect(self._stop)
        self.hist.activated.connect(self._show_history)
        save_out.clicked.connect(self._save_output)
        clear.clicked.connect(lambda: (self.result.set_text(""), self.status.setText("")))
        for w in (self.f_script.textChanged, self.f_kind.changed):
            w.connect(self._update_preview)
        for key, fn in (("Ctrl+Return", self._run), ("Ctrl+Enter", self._run), ("Ctrl+S", self._save)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.activated.connect(fn)
        self.ctx.state.currentChanged.connect(lambda *_: self._update_target())

        self._cur: Command | None = None
        self._stream = None
        self._gen = 0
        self._running = False
        self._fill_tree()
        first = next((c for c in store.all()), None)
        self._select(first.id if first else None)

    # 命令库树 --------------------------------------------------------
    def _fill_tree(self, *_):
        store = self.ctx.commands
        q = self.search.text().lower()
        keep = self._cur.id if getattr(self, "_cur", None) else None
        self.tree.blockSignals(True)
        self.tree.clear()
        t = ui.theme()
        n = 0
        for g in store.groups():
            cmds = [c for c in store.all() if c.group == g and (not q or q in c.name.lower() or q in c.script.lower())]
            if not cmds:
                continue
            gi = W.QTreeWidgetItem([f"{g}  ·  {len(cmds)}"])
            gi.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = gi.font(0)
            f.setBold(True)
            gi.setFont(0, f)
            self.tree.addTopLevelItem(gi)
            for c in cmds:
                it = W.QTreeWidgetItem([c.name])
                it.setData(0, ROLE_ID, c.id)
                it.setToolTip(0, c.script if not c.description else f"{c.description}\n\n{c.script}")
                it.setIcon(0, t.icon("lock" if c.builtin else ("play" if c.persistent else "terminal"), "ink-faint"))
                gi.addChild(it)
                n += 1
                if c.id == keep:
                    self.tree.setCurrentItem(it)
            gi.setExpanded(bool(q) or not cmds[0].builtin or g == store.groups()[0])
        self.tree.blockSignals(False)
        self.lib_head.set_count(n)
        self.search.set_count(n if q else None, len(store.all()))
        groups = store.groups()
        cur = self.f_group.currentText()
        self.f_group.clear()
        self.f_group.addItems([g for g in groups if not any(c.builtin and c.group == g for c in store.all())] or ["我的命令"])
        self.f_group.setCurrentText(cur or "我的命令")

    # 编辑 ------------------------------------------------------------
    def _select(self, cid: str | None):
        c = self.ctx.commands.get(cid) if cid else None
        if c is None:
            return
        self._cur = c
        self.f_name.edit.setText(c.name)
        self.f_group.setCurrentText(c.group)
        self.f_kind.set_value(c.kind)
        self.f_script.setPlainText(c.script)
        self.f_confirm.setChecked(c.confirm)
        self.f_persist.setChecked(c.persistent)
        self.f_stop.setChecked(c.stop_on_error)
        self.f_timeout.setCurrentIndex(max(0, self.f_timeout.findData(str(c.timeout))))
        self.f_desc.edit.setText(c.description)
        ro = c.builtin
        self.ro_badge.setVisible(ro)
        for w in (self.f_name.edit, self.f_desc.edit):
            w.setReadOnly(ro)
        self.f_script.setReadOnly(ro)
        ui.repolish(self.f_script, readonly=ro)
        for w in (self.f_group, self.f_kind, self.f_confirm, self.f_persist, self.f_stop, self.f_timeout):
            w.setEnabled(not ro)
        self.save_btn.setEnabled(not ro)
        self.del_btn.setEnabled(not ro)
        self.up_btn.setEnabled(not ro)
        self.down_btn.setEnabled(not ro)
        self.copy_btn.setToolTip("复制为我的命令（之后可修改）" if ro else "复制一份")
        self._update_preview()

    def _form(self) -> Command:
        c = self._cur
        return Command(id=c.id if c and not c.builtin else self.ctx.commands.new_id(),
                       name=self.f_name.edit.text().strip(), script=self.f_script.toPlainText().rstrip(),
                       kind=self.f_kind.value(), group=self.f_group.currentText().strip() or "我的命令",
                       description=self.f_desc.edit.text().strip(), confirm=self.f_confirm.isChecked(),
                       timeout=int(self.f_timeout.currentData()), persistent=self.f_persist.isChecked(),
                       stop_on_error=self.f_stop.isChecked())

    def _new(self):
        c = Command(id=self.ctx.commands.new_id(), name="新命令", script="getprop ro.build.fingerprint",
                    group=self.f_group.currentText() if self._cur and not self._cur.builtin else "我的命令")
        self.ctx.commands.upsert(c)
        self._select(c.id)
        self._fill_tree()
        self.f_name.edit.setFocus()
        self.f_name.edit.selectAll()

    def _save(self):
        if self._cur is None or self._cur.builtin:
            return
        c = self._form()
        err = None if c.name else "名称不能为空"
        self.f_name.set_error(err)
        if err or not c.script.strip():
            if not c.script.strip():
                self.ctx.notify("warn", "命令内容不能为空")
            return
        self.ctx.commands.upsert(c)
        self._cur = c
        self.ctx.notify("ok", "已保存", c.name)

    def _duplicate(self):
        if self._cur is None:
            return
        base = self._cur if self._cur.builtin else self._form()
        c = self.ctx.commands.duplicate(base)
        self._select(c.id)
        self._fill_tree()

    def _delete(self):
        c = self._cur
        if c and not c.builtin and ui.confirm(self, f"删除“{c.name}”？", "命令将从命令库中删除。", "删除", danger=True):
            self.ctx.commands.delete(c.id)
            nxt = next(iter(self.ctx.commands.all()), None)
            self._cur = None
            if nxt:
                self._select(nxt.id)

    def _update_preview(self, *_):
        kind = self.f_kind.value()
        lines = [l.strip() for l in self.f_script.toPlainText().splitlines() if l.strip() and not l.strip().startswith("#")]
        prefix = "adb -s <设备> shell " if kind == "shell" else "adb -s <设备> "
        self.preview.setText("\n".join(prefix + l for l in lines[:4]) + ("\n…" if len(lines) > 4 else ""))
        self._update_target()

    def _update_target(self):
        d = self.ctx.state.current
        ok = d is not None and d.is_adb
        self.target.setText(f"→ {d.serial}" if ok else "no device")
        self.target.setToolTip(f"执行目标：{d.display_name}（{d.serial}）" if ok else "没有可用的设备")
        ui.repolish(self.target, tone=None if ok else "warn")
        if not self._running:
            self.run_btn.setEnabled(ok)

    # 执行 ------------------------------------------------------------
    def _ask_values(self, c: Command) -> dict[str, str] | None:
        names = c.placeholders
        if not names:
            return {}
        d = Dialog(self, f"执行：{c.name}", "执行")
        fields = {}
        for n in names:
            f = ui.TextField(n, "", mono=True)
            f.edit.setText(self.ctx.commands.last_values.get(n, ""))
            fields[n] = f
            d.add(f)
        prev = d.add_text("")

        def upd(*_):
            vals = {n: f.edit.text() for n, f in fields.items()}
            prev.setText("<code>" + "<br>".join(c.lines(vals)).replace("<", "&lt;") + "</code>")
        for f in fields.values():
            f.edit.textChanged.connect(upd)
        upd()

        def validate():
            err = None
            for f in fields.values():
                e = None if f.edit.text().strip() else "不能为空"
                f.set_error(e)
                err = err or e
            return err
        d.validate = validate
        next(iter(fields.values())).edit.setFocus()
        if not d.run():
            return None
        vals = {n: f.edit.text().strip() for n, f in fields.items()}
        self.ctx.commands.remember(vals)
        return vals

    def _run(self):
        if self._running or self._cur is None:
            return
        d = self.ctx.state.current
        if d is None or not d.is_adb:
            self.ctx.notify("warn", "没有可用的设备", "连接设备并在设备总览中选中后再执行。")
            return
        c = self._cur if self._cur.builtin else self._form()
        if not c.script.strip():
            return
        vals = self._ask_values(c)
        if vals is None:
            return
        lines = c.lines(vals)
        if not lines:
            return
        if c.confirm and not ui.confirm(self, f"执行“{c.name}”？", f"将在 <b>{d.display_name}</b> 上执行：<br><code>"
                                        + "<br>".join(lines).replace("<", "&lt;") + "</code>", "执行", danger=True):
            return
        self.result.set_text("")
        item = HistoryItem(c.name, lines, d.serial, time.time())
        self.result.append(f"# {c.name} · {d.display_name}（{d.serial}）· {time.strftime('%H:%M:%S')}\n", "system")
        if c.persistent:
            self._run_persistent(c, lines[0], d.serial, item)
        else:
            self._run_lines(c, lines, d.serial, item)

    def _args(self, kind: str, line: str) -> list[str]:
        return ["shell", line] if kind == "shell" else shlex.split(line, posix=True)

    def _cmdline(self, kind: str, serial: str, line: str) -> str:
        return f"$ adb -s {serial} {'shell ' if kind == 'shell' else ''}{line}\n"

    def _set_running(self, on: bool, persistent: bool = False):
        self._running = on
        self.run_btn.setEnabled(not on)
        self.stop_btn.setVisible(on and persistent)
        set_icon(self.run_btn, "play", "on-signal", hover="on-signal")
        self.run_btn.setText("Running" if on else "Run")
        if not on:
            self._update_target()

    def _run_lines(self, c: Command, lines: list[str], serial: str, item: HistoryItem):
        b = self.ctx.backend
        self._set_running(True)
        self.status.setText("running…")

        def work(progress):
            for line in lines:
                progress(("start", line))
                t0 = time.time()
                try:
                    r = b.shell(serial, line, c.timeout) if c.kind == "shell" else b.run_adb(serial, self._args("adb", line), c.timeout)
                    res = (line, r.exit_code, r.stdout, r.stderr, time.time() - t0)
                except Exception as e:  # noqa: BLE001 — 单行失败也要显示
                    res = (line, None, "", f"{e}\n{getattr(e, 'detail', '')}".strip() + "\n", time.time() - t0)
                progress(("done", res))
                if c.stop_on_error and res[1] != 0 and len(lines) > 1 and line != lines[-1]:
                    progress(("stopped", line))
                    break

        def step(ev):
            kind, data = ev
            if kind == "start":
                self.result.append(self._cmdline(c.kind, serial, data), "input")
            elif kind == "done":
                line, code, out, err, dt = data
                item.results.append(data)
                if out:
                    self.result.append(out if out.endswith("\n") else out + "\n", "output")
                if err:
                    self.result.append(err if err.endswith("\n") else err + "\n", "error")
                self.result.append(f"→ 退出码 {code if code is not None else '—'} · {dt:.2f} 秒\n\n", "ok" if code == 0 else "error")
            elif kind == "stopped":
                self.result.append("已停止：上一行失败（“某行失败就停止”已开启）\n", "system")

        def done(_):
            self._set_running(False)
            self._finish(item)

        def fail(e):
            self._set_running(False)
            self.ctx.report(e)
        self.ctx.run(work, done=done, error=fail, progress=step)

    def _run_persistent(self, c: Command, line: str, serial: str, item: HistoryItem):
        self._gen += 1
        g = self._gen
        self._set_running(True, persistent=True)
        self.status.setText("● running")
        ui.repolish(self.status, tone="ok")
        self.result.append(self._cmdline(c.kind, serial, line), "input")
        t0 = time.time()
        chunks: list[str] = []
        errs: list[str] = []

        def out(text, stream):
            if g != self._gen:
                return
            (errs if stream == "stderr" else chunks).append(text)
            self.result.append(text, "error" if stream == "stderr" else "output")

        def ex(code, err):
            if g != self._gen:
                return
            self._stream = None
            self._set_running(False)
            if err:
                self.result.append(f"{err}\n", "error")
            self.result.append(f"→ {'已停止' if code is None else f'退出码 {code}'} · {time.time() - t0:.1f} 秒\n", "system")
            item.results.append((line, code, "".join(chunks)[-200_000:], "".join(errs), time.time() - t0))
            self._finish(item)
        try:
            self._stream = self.ctx.backend.start_command(serial, self._args(c.kind, line), on_ui(out), on_ui(ex))
        except Exception as e:  # noqa: BLE001
            self._set_running(False)
            self.ctx.report(e, "无法执行命令")

    def _stop(self):
        if self._stream is not None:
            self._stream.stop()

    def _finish(self, item: HistoryItem):
        code = item.exit_code
        n = len(item.results)
        total = sum(r[4] for r in item.results)
        self.status.setText(f"{'✓' if code == 0 else '✗'} exit {code if code is not None else '—'} · {n} line{'s' if n != 1 else ''} · {total:.2f}s")
        ui.repolish(self.status, tone="ok" if code == 0 else "danger" if code is not None else None)
        self.ctx.commands.add_history(item)
        self._fill_history()

    # 历史 ------------------------------------------------------------
    def _fill_history(self):
        self.hist.blockSignals(True)
        self.hist.clear()
        self.hist.addItem(f"History ({len(self.ctx.commands.history)})", -1)
        for i, h in enumerate(self.ctx.commands.history):
            code = h.exit_code
            self.hist.addItem(f"{time.strftime('%H:%M:%S', time.localtime(h.started))}  {h.command}  ·  {code if code is not None else '—'}", i)
        self.hist.blockSignals(False)

    def _show_history(self, idx: int):
        i = self.hist.itemData(idx)
        if i is None or i < 0:
            return
        h = self.ctx.commands.history[i]
        self.result.set_text("")
        self.result.append(f"# {h.command} · {h.serial} · {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(h.started))}（历史记录）\n", "system")
        for line, code, out, err, dt in h.results:
            self.result.append(f"$ {line}\n", "input")
            if out:
                self.result.append(out if out.endswith("\n") else out + "\n", "output")
            if err:
                self.result.append(err if err.endswith("\n") else err + "\n", "error")
            self.result.append(f"→ 退出码 {code if code is not None else '—'} · {dt:.2f} 秒\n\n", "ok" if code == 0 else "error")
        self.hist.setCurrentIndex(0)

    def _save_output(self):
        text = self.result.plain()
        if not text.strip():
            return
        name = f"adb-{(self._cur.name if self._cur else 'output')}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        name = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in name)
        path, _ = W.QFileDialog.getSaveFileName(self, "保存结果", str(self.ctx.settings.export_dir() / name), "文本文件 (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.ctx.notify("ok", "已保存", path)

    # 导入导出 --------------------------------------------------------
    def _import(self):
        path, _ = W.QFileDialog.getOpenFileName(self, "导入命令", "", "JSON (*.json)")
        if path:
            try:
                n = self.ctx.commands.import_json(path)
            except (OSError, ValueError) as e:
                self.ctx.notify("danger", "导入失败", str(e))
                return
            self.ctx.notify("ok", f"已导入 {n} 条命令" if n else "没有新的命令（同名同内容的已跳过）")

    def _export(self):
        if not self.ctx.commands.user:
            self.ctx.notify("info", "还没有“我的命令”可以导出")
            return
        path, _ = W.QFileDialog.getSaveFileName(self, "导出我的命令", str(self.ctx.settings.export_dir() / "adb-commands.json"), "JSON (*.json)")
        if path:
            n = self.ctx.commands.export_json(path)
            self.ctx.notify("ok", f"已导出 {n} 条命令", path)

    def shown(self):
        self._update_target()
