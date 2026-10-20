"""ADB 命令：命令库（内置预设 + 我的命令）、编辑保存、参数占位、对当前设备执行、着色结果与执行历史。

编辑：新建是未保存的草稿，Save 后才进入命令库；有未保存的修改时切换命令会询问保存 / 放弃；
“Param” 菜单插入各类参数，命令框里参数与注释高亮，保存前用 Command.problems() 校验。
命令树右键：运行、复制命令行、复制、移到分组、上移 / 下移、删除；分组右键：新建、重命名、删除分组。
"""
from __future__ import annotations

import re
import shlex
import time
from dataclasses import replace

import tbui as ui
from tbui._qt import QtGui, QtWidgets, Qt

from ..core.commands import Command, HistoryItem
from ..core.jobs import on_ui
from ..ui.command_params import TYPE_TEXT, ask_param_values
from ..ui.common import PanelHead, TextViewer, ask_choice, ask_text, hbox, label, narrow_select, set_icon, tbtn
from .base import Page

W = QtWidgets
ROLE_ID = Qt.ItemDataRole.UserRole + 1
ROLE_GROUP = Qt.ItemDataRole.UserRole + 2
TIMEOUTS = [("10", "10s", "10 秒"), ("30", "30s", "30 秒"), ("60", "60s", "60 秒"), ("120", "2m", "2 分钟"),
            ("300", "5m", "5 分钟"), ("600", "10m", "10 分钟")]
# Param 菜单：(文字, 插入内容, 插入后选中的部分)
INSERTS = [("文本", "{名称}", "名称"), ("包名", "{包名:package}", "包名"), ("Binder 服务", "{服务:service}", "服务"),
           ("init 服务", "{服务:init}", "服务"), ("PID", "{PID:pid}", "PID"), ("系统属性", "{属性:prop}", "属性"),
           ("电脑上的文件", "{文件:file}", "文件"), ("电脑上的目录", "{目录:dir}", "目录"), None,
           ("带默认值", "{名称=默认值}", "默认值"), ("固定选项", "{选项|a|b|c}", "a|b|c")]
PARAM_RE = re.compile(r"(?<![{$])\{[^\s{}:=|$]+(?::[a-z]+)?(?:=[^{}|]*)?(?:\|[^{}|]*)*\}(?!\})")


class ScriptHighlighter(QtGui.QSyntaxHighlighter):
    """命令框：参数（{…}）着色加粗，# 注释行变淡。"""

    def highlightBlock(self, text: str):
        t = ui.theme()
        if text.lstrip().startswith("#"):
            f = QtGui.QTextCharFormat()
            f.setForeground(t.color("ink-faint"))
            self.setFormat(0, len(text), f)
            return
        f = QtGui.QTextCharFormat()
        f.setForeground(t.color("info"))
        f.setFontWeight(QtGui.QFont.Weight.DemiBold)
        for m in PARAM_RE.finditer(text):
            self.setFormat(m.start(), m.end() - m.start(), f)

    def on_theme(self, *_):
        self.rehighlight()


def shell_quote_line(serial: str, kind: str, line: str) -> str:
    """可直接粘贴到 Git Bash / 终端的完整 adb 命令行。"""
    return f"adb -s {serial} shell {shlex.quote(line)}" if kind == "shell" else f"adb -s {serial} {line}"


def html_lines(lines: list[str]) -> str:
    """命令行逐行转义后用 <br> 连接（放进确认框的富文本）。"""
    return "<br>".join(l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for l in lines)


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
        new = ui.icon_button("plus", "新建命令（Ctrl+N）")
        io = ui.menu([("import", "导入 JSON…", "upload"), ("export", "导出我的命令…", "download"), None,
                      ("clear_history", "清空执行历史", "eraser")], self)
        io.triggered.connect(self._io_action)
        io_btn = ui.icon_button("ellipsis", "导入 / 导出")
        io_btn.setMenu(io)
        io_btn.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.lib_head = PanelHead("命令库", new, io_btn)
        ll.addWidget(self.lib_head)
        fb = W.QWidget()
        fl = W.QHBoxLayout(fb)
        fl.setContentsMargins(8, 8, 8, 8)
        self.search = ui.FilterField("搜索名称、命令或说明", width=240)
        fl.addWidget(self.search)
        ll.addWidget(fb)
        self.tree = W.QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
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
        self.f_group.setToolTip("选择已有分组，或直接输入新分组名")
        self.f_kind = ui.SegmentedControl([("shell", "adb shell"), ("adb", "adb")], "shell", mono=True, label="类型")
        self.f_kind.setToolTip("adb shell：在设备上执行每一行；adb：执行 adb 子命令（install / push / pull / forward …）")
        row1.addWidget(self.f_name, 1)
        row1.addLayout(self._labeled("分组", self.f_group))
        row1.addLayout(self._labeled("类型", self.f_kind))
        ev.addLayout(row1)

        slbl = label("命令（多行逐行执行；# 开头为注释）")
        ui.repolish(slbl, fieldlabel=True)
        self.insert_btn = tbtn("Param", "插入参数：执行时弹框填写，包名 / 服务 / PID / 属性可从设备列表选择", icon="braces")
        im = W.QMenu(self.insert_btn)
        for it in INSERTS:
            if it is None:
                im.addSeparator()
                continue
            text, snippet, sel = it
            a = im.addAction(f"{text}    {snippet}")
            a.triggered.connect(lambda _=False, s=snippet, k=sel: self._insert_param(s, k))
        self.insert_btn.setMenu(im)
        ev.addWidget(hbox(slbl, None, self.insert_btn, spacing=6))
        self.f_script = W.QPlainTextEdit()
        self.f_script.setFont(ui.theme().mono_font(13))
        self.f_script.setMinimumHeight(84)
        self.f_script.setMaximumHeight(160)
        self.f_script.setTabChangesFocus(True)
        self.f_script.setPlaceholderText("dumpsys meminfo {包名:package}")
        ui.repolish(self.f_script, field=True)
        self._hl = ScriptHighlighter(self.f_script.document())
        ui.theme().changed.connect(self._hl.on_theme)
        ev.addWidget(self.f_script)
        self.params_lbl = label("", "faint", px=11)
        self.params_lbl.setWordWrap(True)
        self.problems_lbl = label("", px=11)
        self.problems_lbl.setWordWrap(True)
        ui.repolish(self.problems_lbl, tone="danger")
        self.preview = label("", "faint", mono=True, px=11, select=True)
        self.preview.setWordWrap(True)
        for w in (self.params_lbl, self.problems_lbl, self.preview):
            ev.addWidget(w)
        opts = W.QHBoxLayout()
        opts.setSpacing(20)
        self.f_confirm = ui.Switch("Confirm")
        self.f_confirm.setToolTip("执行前弹出确认框（危险命令用）")
        self.f_persist = ui.Switch("Persistent")
        self.f_persist.setToolTip("持续运行：不限时，执行中可点 Stop 停止（只能有一行命令）")
        self.f_stop = ui.Switch("Stop on error", True)
        self.f_stop.setToolTip("多行命令中某行失败（退出码非 0）就停止")
        self.f_root = ui.Switch("Root")
        self.f_root.setToolTip("需要 root：设备没有 adb root 时，执行前提示先 root")
        self.f_timeout = narrow_select(TIMEOUTS, "30", 64, "单行超时")
        for w in (self.f_confirm, self.f_persist, self.f_stop, self.f_root):
            opts.addWidget(w)
        tl = label("Timeout")
        ui.repolish(tl, formlabel=True)
        opts.addWidget(tl)
        opts.addWidget(self.f_timeout)
        opts.addStretch(1)
        ev.addLayout(opts)
        self.f_desc = ui.TextField("", "说明（可选，显示在命令树的悬停提示里）")
        ev.addWidget(self.f_desc)

        self.run_btn = tbtn("Run", "对当前设备执行（Ctrl+Enter）；我的命令按编辑框里的内容执行，不必先保存", "primary", icon="play", width=72)
        self.save_btn = tbtn("Save", "保存（Ctrl+S）", icon="save", width=68)
        self.copy_btn = tbtn("Duplicate", "复制为我的命令", icon="copy")
        self.del_btn = tbtn("Delete", "删除这条命令", "ghost", icon="trash-2")
        self.up_btn = ui.icon_button("chevron-up", "上移")
        self.down_btn = ui.icon_button("chevron-down", "下移")
        self.cmdline_btn = ui.icon_button("terminal", "复制完整 adb 命令行（参数用上次填写的值 / 默认值），可粘贴到终端")
        self.state_badge = ui.badge("", "info")
        self.target = label("", "caption")
        ev.addWidget(hbox(self.run_btn, self.save_btn, self.copy_btn, self.del_btn, self.up_btn, self.down_btn,
                          self.cmdline_btn, self.state_badge, None, self.target, spacing=4))
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
        self.hist.setToolTip("执行历史（最近 50 条，重启后保留）：选择查看结果")
        self.rerun_btn = ui.icon_button("rotate-ccw", "按相同参数重新执行最近查看的历史记录")
        self.rerun_btn.setEnabled(False)
        rl.addWidget(PanelHead("执行结果", self.status, self.stop_btn, self.hist, self.rerun_btn))
        self.result = TextViewer("执行结果显示在这里")
        tb = self.result.toolbar
        save_out = tb.add(ui.icon_button("save", "保存结果为 txt"))
        clear = tb.add(ui.icon_button("eraser", "清空结果"))
        rl.addWidget(self.result, 1)
        right.addWidget(res)
        right.setSizes([400, 400])

        # 信号 --------------------------------------------------------
        store.changed.connect(self._fill_tree)
        self.search.textChanged.connect(self._fill_tree)
        self.tree.currentItemChanged.connect(self._tree_current)
        self.tree.itemDoubleClicked.connect(lambda it, _c: it.data(0, ROLE_ID) and self._run())
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        new.clicked.connect(lambda: self._new())
        self.run_btn.clicked.connect(self._run)
        self.save_btn.clicked.connect(self._save)
        self.copy_btn.clicked.connect(self._duplicate)
        self.del_btn.clicked.connect(lambda: self._cur and self._delete(self._cur))
        self.up_btn.clicked.connect(lambda: self._move(-1))
        self.down_btn.clicked.connect(lambda: self._move(1))
        self.cmdline_btn.clicked.connect(lambda: self._cur and self._copy_cmdline(self._editing()))
        self.stop_btn.clicked.connect(self._stop)
        self.hist.activated.connect(self._show_history)
        self.rerun_btn.clicked.connect(self._rerun)
        save_out.clicked.connect(self._save_output)
        clear.clicked.connect(lambda: (self.result.set_text(""), self.status.setText("")))
        for sig in (self.f_name.edit.textChanged, self.f_group.editTextChanged, self.f_kind.changed, self.f_script.textChanged,
                    self.f_confirm.toggled, self.f_persist.toggled, self.f_stop.toggled, self.f_root.toggled,
                    self.f_timeout.currentIndexChanged, self.f_desc.edit.textChanged):
            sig.connect(self._on_edit)
        for key, fn in (("Ctrl+Return", self._run), ("Ctrl+Enter", self._run), ("Ctrl+S", self._save), ("Ctrl+N", self._new)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self)
            sc.activated.connect(fn)
        self.ctx.state.currentChanged.connect(lambda *_: self._update_target())

        self._cur: Command | None = None     # 正在编辑的命令（草稿时不在命令库里）
        self._draft = False
        self._clean: tuple | None = None     # 载入 / 保存时的表单快照，用于判断是否有未保存的修改
        self._loading = False
        self._stream = None
        self._gen = 0
        self._running = False
        self._hist_index: int | None = None
        self._fill_tree()
        self._fill_history()
        first = next((c for c in store.all()), None)
        if first:
            self._load(first)
            self._select_in_tree(first.id)

    @staticmethod
    def _labeled(text: str, w: W.QWidget) -> W.QVBoxLayout:
        v = W.QVBoxLayout()
        v.setSpacing(4)
        l = label(text)
        ui.repolish(l, fieldlabel=True)
        v.addWidget(l)
        v.addWidget(w)
        v.addStretch(1)
        return v

    # 命令库树 --------------------------------------------------------
    def _user_groups(self) -> list[str]:
        store = self.ctx.commands
        builtin = {c.group for c in store.all() if c.builtin}
        return [g for g in store.groups() if g not in builtin]

    def _fill_tree(self, *_):
        store = self.ctx.commands
        q = self.search.text().lower()
        keep = self._cur.id if getattr(self, "_cur", None) and not getattr(self, "_draft", False) else None
        expanded = {self.tree.topLevelItem(i).data(0, ROLE_GROUP) for i in range(self.tree.topLevelItemCount())
                    if self.tree.topLevelItem(i).isExpanded()}
        first_fill = self.tree.topLevelItemCount() == 0
        self.tree.blockSignals(True)
        self.tree.clear()
        t = ui.theme()
        n = 0
        user_groups = set(self._user_groups())
        for g in store.groups():
            cmds = [c for c in store.all() if c.group == g and (not q or q in c.name.lower() or q in c.script.lower()
                                                                 or q in c.description.lower())]
            if not cmds:
                continue
            gi = W.QTreeWidgetItem([f"{g}  ·  {len(cmds)}"])
            gi.setData(0, ROLE_GROUP, g)
            gi.setFlags(Qt.ItemFlag.ItemIsEnabled)
            f = gi.font(0)
            f.setBold(True)
            gi.setFont(0, f)
            if g in user_groups:
                gi.setToolTip(0, "我的分组：右键可新建命令、重命名或删除分组")
            self.tree.addTopLevelItem(gi)
            for c in cmds:
                it = W.QTreeWidgetItem([c.name])
                it.setData(0, ROLE_ID, c.id)
                tags = " · ".join(x for x in ("需要 root" if c.root else "", "持续运行" if c.persistent else "",
                                              "执行前确认" if c.confirm else "", "adb 子命令" if c.kind == "adb" else "") if x)
                tip = "\n".join(x for x in (c.description, tags, "", c.script) if x is not None)
                it.setToolTip(0, tip.strip())
                icon = "lock" if c.builtin else ("play" if c.persistent else "terminal")
                it.setIcon(0, t.icon(icon, "ink-faint"))
                gi.addChild(it)
                n += 1
                if c.id == keep:
                    self.tree.setCurrentItem(it)
            gi.setExpanded(bool(q) or (g in expanded if not first_fill else (g in user_groups or g == store.groups()[0]))
                           or (keep is not None and any(c.id == keep for c in cmds)))
        self.tree.blockSignals(False)
        self.lib_head.set_count(n)
        self.search.set_count(n if q else None, len(store.all()))
        cur = self.f_group.currentText()
        self._loading, was = True, self._loading
        self.f_group.blockSignals(True)
        self.f_group.clear()
        self.f_group.addItems(self._user_groups() or ["我的命令"])
        self.f_group.setCurrentText(cur or "我的命令")
        self.f_group.blockSignals(False)
        self._loading = was

    def _select_in_tree(self, cid: str | None):
        self.tree.blockSignals(True)
        self.tree.setCurrentItem(None)
        if cid:
            for i in range(self.tree.topLevelItemCount()):
                gi = self.tree.topLevelItem(i)
                for k in range(gi.childCount()):
                    if gi.child(k).data(0, ROLE_ID) == cid:
                        gi.setExpanded(True)
                        self.tree.setCurrentItem(gi.child(k))
        self.tree.blockSignals(False)

    def _select(self, cid: str) -> bool:
        """切换到指定命令（有未保存的修改时先询问）；返回是否已切换。"""
        c = self.ctx.commands.get(cid)
        if c is None or not self._confirm_leave():
            return False
        self._load(c)
        self._select_in_tree(c.id)
        return True

    def _tree_current(self, cur, _prev):
        cid = cur.data(0, ROLE_ID) if cur else None
        if not cid or (self._cur and cid == self._cur.id and not self._draft):
            return
        if not self._confirm_leave():
            self._select_in_tree(None if self._draft else (self._cur.id if self._cur else None))
            return
        c = self.ctx.commands.get(cid)
        if c:
            self._load(c)

    def _tree_menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        store = self.ctx.commands
        m = W.QMenu(self)
        t = ui.theme()
        cid = it.data(0, ROLE_ID)
        if cid:
            c = store.get(cid)
            if c is None:
                return
            m.addAction(t.icon("play", "ink-muted"), "运行", lambda: self._run_from_tree(c))
            m.addAction(t.icon("terminal", "ink-muted"), "复制命令行", lambda: self._copy_cmdline(c))
            m.addSeparator()
            m.addAction(t.icon("copy", "ink-muted"), "复制为我的命令" if c.builtin else "复制一份", lambda: self._duplicate_cmd(c))
            if not c.builtin:
                mv = m.addMenu("移到分组")
                for g in self._user_groups():
                    a = mv.addAction(g, lambda g=g: self._move_to_group(c, g))
                    a.setEnabled(g != c.group)
                mv.addSeparator()
                mv.addAction("新分组…", lambda: self._move_to_new_group(c))
                m.addAction(t.icon("chevron-up", "ink-muted"), "上移", lambda: store.move(c.id, -1))
                m.addAction(t.icon("chevron-down", "ink-muted"), "下移", lambda: store.move(c.id, 1))
                m.addSeparator()
                m.addAction(t.icon("trash-2", "danger"), "删除", lambda: self._delete(c))
        else:
            g = it.data(0, ROLE_GROUP)
            if g in self._user_groups():
                m.addAction(t.icon("plus", "ink-muted"), "在此分组新建命令", lambda: self._new(g))
                m.addAction(t.icon("pencil", "ink-muted"), "重命名分组…", lambda: self._rename_group(g))
                m.addSeparator()
                m.addAction(t.icon("trash-2", "danger"), "删除分组…", lambda: self._delete_group(g))
            else:
                m.addAction(t.icon("plus", "ink-muted"), "新建我的命令", lambda: self._new())
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # 编辑 ------------------------------------------------------------
    def _snapshot(self) -> tuple:
        return (self.f_name.edit.text().strip(), self.f_group.currentText().strip(), self.f_kind.value(),
                self.f_script.toPlainText().rstrip(), self.f_confirm.isChecked(), self.f_persist.isChecked(),
                self.f_stop.isChecked(), self.f_root.isChecked(), self.f_timeout.currentData(), self.f_desc.edit.text().strip())

    def _dirty(self) -> bool:
        if self._cur is None or self._cur.builtin:
            return False
        return self._draft or self._snapshot() != self._clean

    def _load(self, c: Command, draft: bool = False):
        self._loading = True
        self._cur, self._draft = c, draft
        self.f_name.edit.setText(c.name)
        self.f_name.set_error(None)
        self.f_group.setCurrentText(c.group)
        self.f_kind.set_value(c.kind)
        self.f_script.setPlainText(c.script)
        self.f_confirm.setChecked(c.confirm)
        self.f_persist.setChecked(c.persistent)
        self.f_stop.setChecked(c.stop_on_error)
        self.f_root.setChecked(c.root)
        self.f_timeout.setCurrentIndex(max(0, self.f_timeout.findData(str(c.timeout))))
        self.f_desc.edit.setText(c.description)
        ro = c.builtin
        for w in (self.f_name.edit, self.f_desc.edit):
            w.setReadOnly(ro)
        self.f_script.setReadOnly(ro)
        ui.repolish(self.f_script, readonly=ro)
        for w in (self.f_group, self.f_kind, self.f_confirm, self.f_persist, self.f_stop, self.f_root, self.f_timeout, self.insert_btn):
            w.setEnabled(not ro)
        self.del_btn.setEnabled(not ro and not draft)
        self.up_btn.setEnabled(not ro and not draft)
        self.down_btn.setEnabled(not ro and not draft)
        self.copy_btn.setEnabled(not draft)
        self.copy_btn.setText("Edit copy" if ro else "Duplicate")
        self.copy_btn.setToolTip("内置命令只读：复制为我的命令后即可修改" if ro else "复制一份")
        self._loading = False
        self._clean = self._snapshot()
        self._on_edit()

    def _on_edit(self, *_):
        if self._loading or self._cur is None:
            return
        c = self._editing()
        dirty = self._dirty()
        self.save_btn.setEnabled(dirty)
        if self._cur.builtin:
            self.state_badge.setText("built-in · read-only")
            self.state_badge.setToolTip("内置命令只读：可以直接执行；要修改请点 Edit copy 复制为我的命令")
        elif self._draft:
            self.state_badge.setText("new · unsaved")
            self.state_badge.setToolTip("新命令还没有保存到命令库（Ctrl+S 保存）")
        else:
            self.state_badge.setText("unsaved")
            self.state_badge.setToolTip("有未保存的修改（Ctrl+S 保存）")
        self.state_badge.setVisible(self._cur.builtin or dirty)
        # 参数与校验
        ps = c.params
        if ps:
            parts = []
            for p in ps:
                s = p.name
                if p.choices:
                    s += "（" + " / ".join(p.choices) + "）"
                elif p.type != "text":
                    s += f"（{TYPE_TEXT.get(p.type, p.type)}）"
                if p.default and not p.choices:
                    s += f" = {p.default}"
                parts.append(s)
            self.params_lbl.setText("参数：" + "、".join(parts) + "　执行时弹框填写")
        self.params_lbl.setVisible(bool(ps))
        probs = [] if self._cur.builtin else c.problems()
        self.problems_lbl.setText("\n".join("⚠ " + p for p in probs))
        self.problems_lbl.setVisible(bool(probs))
        lines = c.lines()
        prefix = "adb -s <设备> shell " if c.kind == "shell" else "adb -s <设备> "
        self.preview.setText("\n".join(prefix + l for l in lines[:4]) + ("\n…" if len(lines) > 4 else ""))
        self._update_target()

    def _editing(self) -> Command:
        """当前编辑框里的命令（内置命令原样返回）。"""
        c = self._cur
        if c is None or c.builtin:
            return c
        name, group, kind, script, confirm, persistent, stop, root, timeout, desc = self._snapshot()
        return replace(c, name=name, group=group or "我的命令", kind=kind, script=script, description=desc, confirm=confirm,
                       persistent=persistent, stop_on_error=stop, root=root, timeout=int(timeout))

    def _insert_param(self, snippet: str, select: str):
        cur = self.f_script.textCursor()
        cur.insertText(snippet)
        end = cur.position()
        start = end - len(snippet) + snippet.index(select)
        cur.setPosition(start)
        cur.setPosition(start + len(select), QtGui.QTextCursor.MoveMode.KeepAnchor)
        self.f_script.setTextCursor(cur)
        self.f_script.setFocus()

    def _confirm_leave(self) -> bool:
        """有未保存的修改时询问；返回 False 表示留在当前命令。"""
        if not self._dirty():
            return True
        c = self._editing()
        name = c.name or "新命令"
        ans = ask_choice(self, "保存修改？", f"“{name}”有未保存的修改。", [("save", "保存", "primary"), ("discard", "放弃修改", "secondary")])
        if ans == "save":
            return self._save()
        return ans == "discard"

    def _new(self, group: str | None = None):
        if not self._confirm_leave():
            return
        if group is None:
            group = self._cur.group if self._cur and not self._cur.builtin else (self._user_groups() or ["我的命令"])[0]
        c = Command(id=self.ctx.commands.new_id(), name="", script="", group=group)
        self._select_in_tree(None)
        self._load(c, draft=True)
        self.f_name.edit.setFocus()

    def _save(self) -> bool:
        if self._cur is None or self._cur.builtin:
            return False
        c = self._editing()
        err = None if c.name else "名称不能为空"
        self.f_name.set_error(err)
        if err:
            self.f_name.edit.setFocus()
            return False
        if not c.script.strip():
            self.ctx.notify("warn", "命令内容不能为空")
            self.f_script.setFocus()
            return False
        probs = c.problems()
        if probs:
            self.ctx.notify("warn", "命令有问题，未保存", probs[0])
            return False
        self.ctx.commands.upsert(c)
        self._load(c)
        self._select_in_tree(c.id)
        self.ctx.notify("ok", "已保存", c.name)
        return True

    def _duplicate(self):
        if self._cur is not None and not self._draft:
            self._duplicate_cmd(self._cur if self._cur.builtin else self._editing())

    def _duplicate_cmd(self, base: Command):
        if not self._confirm_leave():
            return
        c = self.ctx.commands.duplicate(base)
        self._load(c)
        self._select_in_tree(c.id)
        if base.builtin:
            self.ctx.notify("info", "已复制为我的命令", "现在可以修改并保存")

    def _delete(self, c: Command):
        if c.builtin or not ui.confirm(self, f"删除“{c.name}”？", "命令将从命令库中删除。", "删除", danger=True):
            return
        store = self.ctx.commands
        same = [x for x in store.all() if x.group == c.group]
        i = next((k for k, x in enumerate(same) if x.id == c.id), 0)
        store.delete(c.id)
        rest = [x for x in store.all() if x.group == c.group]
        nxt = (rest[min(i, len(rest) - 1)] if rest else next(iter(store.all()), None))
        if self._cur and self._cur.id == c.id:
            self._cur = None
            if nxt:
                self._load(nxt)
                self._select_in_tree(nxt.id)

    def _move(self, delta: int):
        if self._cur and not self._cur.builtin and not self._draft:
            self.ctx.commands.move(self._cur.id, delta)

    def _move_to_group(self, c: Command, group: str):
        store = self.ctx.commands
        cur = store.get(c.id)
        if cur is None:
            return
        store.upsert(replace(cur, group=group))
        if self._cur and self._cur.id == c.id:
            self._loading = True
            self.f_group.setCurrentText(group)
            self._loading = False
            self._cur = replace(self._cur, group=group)
            if self._clean:
                self._clean = self._clean[:1] + (group,) + self._clean[2:]
            self._on_edit()
        self._select_in_tree(self._cur.id if self._cur and not self._draft else None)

    def _check_group_name(self, name: str) -> str | None:
        name = name.strip()
        if not name:
            return "不能为空"
        if any(c.builtin and c.group == name for c in self.ctx.commands.all()):
            return "与内置分组同名"
        return None

    def _move_to_new_group(self, c: Command):
        g = ask_text(self, "移到新分组", "分组名", "", "例如：音频模块", mono=False, check=self._check_group_name)
        if g:
            self._move_to_group(c, g)

    def _rename_group(self, old: str):
        new = ask_text(self, "重命名分组", "分组名", old, mono=False, check=self._check_group_name)
        if not new or new == old:
            return
        store = self.ctx.commands
        for c in [x for x in store.user if x.group == old]:
            store.upsert(replace(c, group=new))
        if self._cur and not self._cur.builtin and self._cur.group == old:
            self._cur = replace(self._cur, group=new)
            self._loading = True
            self.f_group.setCurrentText(new)
            self._loading = False
            if self._clean:
                self._clean = self._clean[:1] + (new,) + self._clean[2:]
            self._on_edit()
        self.ctx.notify("ok", "已重命名分组", f"{old} → {new}")

    def _delete_group(self, g: str):
        store = self.ctx.commands
        cmds = [x for x in store.user if x.group == g]
        if not cmds or not ui.confirm(self, f"删除分组“{g}”？", f"分组内的 {len(cmds)} 条命令都会被删除。", "删除", danger=True):
            return
        for c in cmds:
            store.delete(c.id)
        if self._cur and not self._draft and self._cur.group == g and not self._cur.builtin:
            nxt = next(iter(store.all()), None)
            self._cur = None
            if nxt:
                self._load(nxt)
                self._select_in_tree(nxt.id)

    def _update_target(self):
        d = self.ctx.state.current
        ok = d is not None and d.is_adb
        self.target.setText(f"→ {d.serial}" if ok else "no device")
        self.target.setToolTip(f"执行目标：{d.display_name}（{d.serial}）" if ok else "没有可用的设备")
        ui.repolish(self.target, tone=None if ok else "warn")
        if not self._running:
            self.run_btn.setEnabled(ok)

    # 执行 ------------------------------------------------------------
    def _copy_cmdline(self, c: Command | None):
        if c is None:
            return
        d = self.ctx.state.current
        serial = d.serial if d is not None and d.is_adb else "<设备>"
        last = self.ctx.commands.last_values
        vals = {p.name: last.get(p.name) or p.default for p in c.params if last.get(p.name) or p.default}
        lines = c.lines(vals)
        if not lines:
            return
        QtWidgets.QApplication.clipboard().setText("\n".join(shell_quote_line(serial, c.kind, l) for l in lines))
        left = [p.name for p in c.params if p.name not in vals]
        self.ctx.notify("ok", "已复制命令行", ("未填写的参数保留为 {…}：" + "、".join(left)) if left else
                        f"{len(lines)} 行，可粘贴到 Git Bash / 终端")

    def _run_from_tree(self, c: Command):
        if (not self._cur or c.id != self._cur.id or self._draft) and not self._select(c.id):
            return
        self._run()

    def _run(self):
        if self._running or self._cur is None:
            return
        d = self.ctx.state.current
        if d is None or not d.is_adb:
            self.ctx.notify("warn", "没有可用的设备", "连接设备并在设备总览中选中后再执行。")
            return
        c = self._editing()
        if not c.script.strip():
            return
        probs = [] if c.builtin else c.problems()
        if probs:
            self.ctx.notify("warn", "命令有问题", probs[0])
            return
        vals = ask_param_values(self, self.ctx, c, d.serial)
        if vals is None:
            return
        lines = c.lines(vals)
        if not lines:
            return
        if c.confirm and not ui.confirm(self, f"执行“{c.name}”？", f"将在 <b>{d.display_name}</b> 上执行：<br><code>"
                                        + html_lines(lines) + "</code>", "执行", danger=True):
            return
        if c.root:
            self._check_root(d, lambda: self._start(c, lines, d, vals))
        else:
            self._start(c, lines, d, vals)

    def _check_root(self, d, proceed):
        """需要 root 的命令：先查 adbd 是否 root；没有时询问 adb root 后执行 / 直接执行。"""
        b = self.ctx.backend
        self.status.setText("checking root…")

        def got(rs):
            self.status.setText("")
            if rs.adbd_root:
                proceed()
                return
            if rs.build_type == "user":
                body = "这台设备是 user 固件，不能 adb root，命令很可能执行失败。"
                ans = ask_choice(self, "需要 root", body, [("run", "仍然执行", "secondary")])
            else:
                ans = ask_choice(self, "需要 root", f"<b>{d.display_name}</b> 当前没有 adb root，这条命令需要 root 权限。",
                                 [("root", "adb root 后执行", "primary"), ("run", "直接执行", "secondary")])
            if ans == "run":
                proceed()
            elif ans == "root":
                self.status.setText("adb root…")
                self.ctx.run(b.root, d.serial, True, done=lambda _: (self.status.setText(""), self.ctx.state.rootChanged.emit(d.serial),
                                                                      proceed()),
                             error=lambda e: (self.status.setText(""), self.ctx.report(e, "adb root 失败")))
        self.ctx.run(b.root_state, d.serial, done=got, error=lambda e: (self.status.setText(""), proceed()))

    def _start(self, c: Command, lines: list[str], d, vals: dict[str, str]):
        self.result.set_text("")
        item = HistoryItem(c.name, lines, d.serial, time.time(), kind=c.kind, command_id=c.id, values=dict(vals))
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
        self.rerun_btn.setEnabled(not on and self._hist_index is not None)
        self.stop_btn.setVisible(on and persistent)
        set_icon(self.run_btn, "play", "on-signal", hover="on-signal")
        self.run_btn.setText("Running" if on else "Run")
        if not on:
            self._update_target()

    def _run_lines(self, c: Command, lines: list[str], serial: str, item: HistoryItem):
        b = self.ctx.backend
        self._set_running(True)
        self.status.setText("running…")
        ui.repolish(self.status, tone=None)

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
        self._hist_index = 0
        self._fill_history()

    # 历史 ------------------------------------------------------------
    def _fill_history(self):
        self.hist.blockSignals(True)
        self.hist.clear()
        self.hist.addItem(f"History ({len(self.ctx.commands.history)})", -1)
        today = time.strftime("%Y-%m-%d")
        for i, h in enumerate(self.ctx.commands.history):
            code = h.exit_code
            ts = time.localtime(h.started)
            when = time.strftime("%H:%M:%S" if time.strftime("%Y-%m-%d", ts) == today else "%m-%d %H:%M", ts)
            self.hist.addItem(f"{when}  {h.command}  ·  {code if code is not None else '—'}", i)
        self.hist.blockSignals(False)
        self.rerun_btn.setEnabled(not self._running and self._hist_index is not None and self._hist_index < len(self.ctx.commands.history))

    def _show_history(self, idx: int):
        i = self.hist.itemData(idx)
        if i is None or i < 0:
            return
        h = self.ctx.commands.history[i]
        self._hist_index = i
        self.result.set_text("")
        self.result.append(f"# {h.command} · {h.serial} · {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(h.started))}（历史记录）\n", "system")
        for line, code, out, err, dt in h.results:
            self.result.append(self._cmdline(h.kind, h.serial, line), "input")
            if out:
                self.result.append(out if out.endswith("\n") else out + "\n", "output")
            if err:
                self.result.append(err if err.endswith("\n") else err + "\n", "error")
            self.result.append(f"→ 退出码 {code if code is not None else '—'} · {dt:.2f} 秒\n\n", "ok" if code == 0 else "error")
        self.hist.setCurrentIndex(0)
        self.rerun_btn.setEnabled(not self._running)
        self.rerun_btn.setToolTip(f"重新执行：{h.command}（相同的命令行，在当前设备上）")

    def _rerun(self):
        """按历史记录里展开后的命令行重新执行（不再弹参数框）。"""
        if self._running or self._hist_index is None or self._hist_index >= len(self.ctx.commands.history):
            return
        d = self.ctx.state.current
        if d is None or not d.is_adb:
            self.ctx.notify("warn", "没有可用的设备", "连接设备并在设备总览中选中后再执行。")
            return
        h = self.ctx.commands.history[self._hist_index]
        src = self.ctx.commands.get(h.command_id) if h.command_id else None
        base = src or Command(id="", name=h.command, script="\n".join(h.lines), kind=h.kind)
        c = replace(base, script="\n".join(h.lines), kind=h.kind)   # 历史里是展开后的命令行，不含参数
        if c.confirm and not ui.confirm(self, f"重新执行“{c.name}”？", f"将在 <b>{d.display_name}</b> 上执行：<br><code>"
                                        + html_lines(h.lines) + "</code>", "执行", danger=True):
            return
        go = lambda: self._start(c, list(h.lines), d, dict(h.values))  # noqa: E731
        self._check_root(d, go) if c.root else go()

    def _save_output(self):
        text = self.result.plain()
        if not text.strip():
            return
        name = f"adb-{(self._cur.name if self._cur else 'output') or 'output'}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        name = "".join(ch if ch not in '\\/:*?"<>|' else "_" for ch in name)
        path, _ = W.QFileDialog.getSaveFileName(self, "保存结果", str(self.ctx.settings.export_dir() / name), "文本文件 (*.txt)")
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(text)
            self.ctx.notify("ok", "已保存", path)

    # 导入导出 --------------------------------------------------------
    def _io_action(self, a):
        key = a.data()
        if key == "import":
            self._import()
        elif key == "export":
            self._export()
        elif key == "clear_history" and self.ctx.commands.history:
            if ui.confirm(self, "清空执行历史？", f"共 {len(self.ctx.commands.history)} 条记录。", "清空", danger=True):
                self.ctx.commands.clear_history()
                self._hist_index = None
                self._fill_history()

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

    def can_close(self) -> bool:
        """主窗口关闭前调用：有未保存的修改时询问保存 / 不保存 / 取消；返回 False 表示取消关闭。"""
        if not self._dirty():
            return True
        ans = ask_choice(self, "保存修改？", f"“{self._editing().name or '新命令'}”有未保存的修改，关闭前要保存吗？",
                         [("save", "保存", "primary"), ("discard", "不保存", "secondary")])
        if ans == "save":
            return self._save()
        return ans == "discard"
