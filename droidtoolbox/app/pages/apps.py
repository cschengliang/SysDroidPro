"""应用管理：按 Android 用户（自动识别）查看包；详情分 概览 / 组件 / 权限 / 原始 dump；操作均针对所选用户；批量 dump。"""
from __future__ import annotations

import re
import threading
from pathlib import Path

import tbui as ui
from tbui._qt import QtWidgets, Qt

from ..backend.api import Cancelled
from ..backend.models import AndroidUser, Component, PackageDetail, PackageInfo, Progress
from ..ui.common import (BusyBar, Column, DataTable, Dialog, SegmentStack, TaskStrip, TextViewer, grow, hbox, label, narrow_select,
                         reveal, tbtn)
from .actions import install_apks
from .base import Page

W = QtWidgets
KIND_TEXT = {"activity": "Activity", "service": "Service", "receiver": "Receiver", "provider": "Provider"}


class AppsPage(Page):
    id = "apps"
    title = "应用管理"
    icon = "package"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        refresh = tb.add(ui.icon_button("refresh-cw", "刷新列表"))
        tb.separator()
        self.user = tb.add(narrow_select([("0", "u0", "用户 0")], "0", 64))
        self.user.setToolTip("Android 用户（自动识别）：机主、工作资料、应用分身、访客等")
        self.filter = tb.add(grow(ui.FilterField("Filter: package / version", width=260), 220))
        self.kind = tb.add(ui.SegmentedControl([("all", "All"), ("third", "3rd"), ("system", "System"), ("disabled", "Disabled")],
                                               "third", label="类型"))
        self.kind.setToolTip("All 全部 · 3rd 第三方 · System 系统 · Disabled 已停用")
        tb.separator()
        dump_all = tb.add(tbtn("Dump all", "批量 dump：逐个执行 dumpsys package 并保存为 txt", icon="file-text"))
        install = tb.add(tbtn("Install", "安装 APK（可选目标用户；也可以把 APK 拖进窗口）", "primary", icon="download"))
        lay.addWidget(tb)
        self.busy = BusyBar()
        lay.addWidget(self.busy)
        self.strip = TaskStrip((12, 8, 12, 8))
        lay.addWidget(self.strip)

        split = W.QSplitter(Qt.Orientation.Horizontal)
        split.setChildrenCollapsible(False)
        self.table = DataTable([
            Column("包名", lambda p: p.name, 300, mono=True, tone=lambda p: "ink-disabled" if not p.enabled else None),
            Column("版本", lambda p: p.version_name, 110, mono=True, tip=lambda p: f"versionCode {p.version_code}"),
            Column("类型", lambda p: "系统" if p.system else "第三方", 64, tone=lambda p: "ink-muted" if p.system else None),
            Column("状态", lambda p: "已启用" if p.enabled else "已停用", None, tone=lambda p: None if p.enabled else "warn"),
        ])
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        split.addWidget(self.table)
        split.addWidget(self._build_detail())
        split.setSizes([560, 640])
        lay.addWidget(split, 1)

        refresh.clicked.connect(self.refresh)
        self.user.currentIndexChanged.connect(lambda *_: self._load_packages())
        self.filter.textChanged.connect(self._apply)
        self.kind.changed.connect(self._apply)
        self.table.selectionChangedRows.connect(lambda rows: self._select(rows[0] if rows else None))
        install.clicked.connect(lambda: install_apks(self, self.serial, self.strip, done=self.refresh, users=self._users,
                                                     current_user=self._uid))
        dump_all.clicked.connect(self._batch_dump)
        self.setAcceptDrops(True)
        self._users: list[AndroidUser] = []
        self._pkg: PackageInfo | None = None
        self._detail: PackageDetail | None = None
        self._loaded_tabs: set[str] = set()      # 当前包已加载的详情标签
        self._token = 0
        self._show_detail(None)

    @property
    def _uid(self) -> int:
        return int(self.user.currentData() or 0)

    # 详情面板 --------------------------------------------------------
    def _build_detail(self) -> W.QWidget:
        box = W.QWidget()
        box.setMinimumWidth(460)
        v = W.QVBoxLayout(box)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        head = W.QWidget()
        head.setProperty("page", True)
        hv = W.QVBoxLayout(head)
        hv.setContentsMargins(20, 16, 20, 12)
        hv.setSpacing(8)
        self.d_name = W.QLabel()
        self.d_name.setFont(ui.theme().display_font(18))
        self.d_name.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.d_name.setWordWrap(True)
        self.d_badges = hbox(spacing=6)
        hv.addWidget(self.d_name)
        hv.addWidget(self.d_badges)
        self.a_launch = tbtn("Launch", "启动（monkey LAUNCHER）", icon="play")
        self.a_stop = tbtn("Stop", "强行停止（am force-stop）", icon="square")
        self.a_toggle = tbtn("Disable", "", icon="power")
        self.a_extract = tbtn("Pull APK", "导出 base.apk 与 split APK 到导出目录", icon="download")
        more = ui.menu([("clear", "清除数据…", "eraser", None, True), None,
                        ("uninstall_user", "为当前用户卸载…", "trash-2", None, True),
                        ("uninstall", "卸载（所有用户）…", "trash-2", None, True)], self)
        self.a_more = tbtn("More", "清除数据 / 卸载", icon="ellipsis", menu=more)
        self.actions = hbox(self.a_launch, self.a_stop, self.a_toggle, self.a_extract, self.a_more, None)
        hv.addWidget(self.actions)
        v.addWidget(head)
        self.a_launch.clicked.connect(lambda: self._simple("launch", "已启动"))
        self.a_stop.clicked.connect(lambda: self._simple("force_stop", "已强行停止"))
        self.a_toggle.clicked.connect(self._toggle)
        self.a_extract.clicked.connect(self._extract)
        more.triggered.connect(lambda a: self._danger(a.data()))

        # 概览
        self.ov_scroll = W.QScrollArea()
        self.ov_scroll.setWidgetResizable(True)
        self.ov_scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        # 组件
        comp = W.QWidget()
        cl = W.QVBoxLayout(comp)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        bar = ui.Toolbar()
        self.c_filter = bar.add(grow(ui.FilterField("Filter: class", width=200), 160))
        self.c_kind = bar.add(narrow_select([("all", "all"), ("activity", "Activity"), ("service", "Service"),
                                         ("receiver", "Receiver"), ("provider", "Provider")], "all", 96, "组件类型"))
        c_on = bar.add(tbtn("Enable", "启用所选组件（pm enable，需要 root）"))
        c_off = bar.add(tbtn("Disable", "停用所选组件（pm disable，需要 root）"))
        cl.addWidget(bar)
        self.c_table = DataTable([
            Column("类型", lambda c: KIND_TEXT[c.kind], 76, tone=lambda c: "ink-muted"),
            Column("组件", lambda c: self._short(c.name), 300, mono=True, tip=lambda c: c.name,
                   tone=lambda c: "ink-disabled" if not c.enabled else None),
            Column("导出", lambda c: "是" if c.exported else ("否" if c.exported is False else "—"), 48,
                   tone=lambda c: "warn" if c.exported else None),
            Column("状态", lambda c: "启用" if c.enabled else "停用", 52, tone=lambda c: None if c.enabled else "warn"),
            Column("权限 / Authority", lambda c: c.permission or c.authority, None, mono=True, tone=lambda c: "ink-muted"),
        ], multi=True)
        cl.addWidget(self.c_table, 1)
        self.c_filter.textChanged.connect(self._apply_comp)
        self.c_kind.currentIndexChanged.connect(self._apply_comp)
        c_on.clicked.connect(lambda: self._set_components(True))
        c_off.clicked.connect(lambda: self._set_components(False))
        # 权限
        self.pm_scroll = W.QScrollArea()
        self.pm_scroll.setWidgetResizable(True)
        self.pm_scroll.setFrameShape(W.QFrame.Shape.NoFrame)
        # 原始 dump
        self.dump = TextViewer("切到这个标签时读取 dumpsys package")
        save = self.dump.toolbar.add(ui.icon_button("save", "保存为 txt"))
        save.clicked.connect(self._save_dump)

        self.tabs = SegmentStack([("overview", "Info", self.ov_scroll), ("components", "Components", comp),
                                  ("perms", "Permissions", self.pm_scroll), ("dump", "Dump", self.dump)])
        self.tabs.changed.connect(self._tab_changed)
        v.addWidget(self.tabs, 1)
        self._detail_box = box
        self._head_box = head
        return box

    def _short(self, name: str) -> str:
        """组件类名省略包名前缀：com.example.shop.MainActivity → ….MainActivity。"""
        pkg = self._detail.info.name if self._detail else ""
        return ("…" + name[len(pkg):]) if pkg and name.startswith(pkg + ".") else name

    # 加载 ------------------------------------------------------------
    def load(self, device):
        self.ctx.run(self.ctx.backend.list_users, device.serial, done=self._got_users, error=lambda e: self._load_packages())

    def unload(self):
        self.table.set_rows([])
        self._show_detail(None)

    def refresh(self):
        self._load_packages()

    def _got_users(self, users: list[AndroidUser]):
        self._users = users
        keep = self.user.currentData()
        self.user.blockSignals(True)
        self.user.clear()
        for u in users:
            self.user.addItem(f"u{u.id}", str(u.id))
            self.user.setItemData(self.user.count() - 1, f"用户 {u.id} · {u.name}（{u.kind}）" + ("" if u.running else " · 未运行"),
                                  Qt.ItemDataRole.ToolTipRole)
        self.user.setCurrentIndex(max(0, self.user.findData(keep)))
        self.user.setEnabled(len(users) > 1)
        self.user.blockSignals(False)
        self._load_packages()

    def _load_packages(self):
        if self.device is None:
            return
        self.busy.push()
        uid = self._uid

        def done(pkgs):
            self.busy.pop()
            if uid != self._uid:
                return
            self.table.set_rows(pkgs, keep=lambda p: p.name)
            self._apply()
            if self.table.current_row() is None:
                self._show_detail(None)

        def fail(e):
            self.busy.pop()
            self.ctx.report(e, "无法读取应用列表")
        self.ctx.run(self.ctx.backend.list_packages, self.serial, uid, done=done, error=fail)

    def _apply(self, *_):
        k = self.kind.value()
        pred = {"all": None, "third": lambda p: not p.system, "system": lambda p: p.system, "disabled": lambda p: not p.enabled}[k]
        self.table.filter.set_predicate(pred)
        self.table.filter.set_text(self.filter.text())
        self.filter.set_count(self.table.filter.rowCount(), self.table.table_model.rowCount())
        self.ctx.window.update_status(self)

    def status_text(self) -> str:
        m = self.table.table_model
        third = sum(1 for p in m.rows if not p.system)
        return f"u{self._uid} · {m.rowCount()} pkgs · 3rd {third} · sys {m.rowCount() - third}"

    # 详情 ------------------------------------------------------------
    def _show_detail(self, d: PackageDetail | None):
        self._detail = d
        has = d is not None
        self._head_box.setVisible(has)
        self.tabs.setVisible(has)
        if not has:
            if not hasattr(self, "_placeholder"):
                self._placeholder = ui.EmptyState("package", "选择一个应用",
                                                  "查看版本、组件、权限和完整的 dumpsys package 输出；操作针对当前所选用户。也可以把 APK 拖进窗口安装。")
                self._detail_box.layout().addWidget(self._placeholder, 1)
            self._placeholder.show()
            return
        if hasattr(self, "_placeholder"):
            self._placeholder.hide()
        p = d.info
        self.d_name.setText(p.name)
        lay = self.d_badges.layout()
        while lay.count():
            w = lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        for w in [ui.badge(p.version_name or "?", mono=True), ui.badge("系统应用" if p.system else "第三方应用"),
                  ui.badge(f"u{self._uid}", "info", mono=True)] + ([ui.badge("已停用", "warn")] if not p.enabled else []) + \
                 ([label(f"uid {p.uid}", "faint", mono=True)] if p.uid is not None else []):
            lay.addWidget(w)
        lay.addStretch(1)
        self.a_launch.setEnabled(p.enabled and d.launchable)
        self.a_toggle.setText("Enable" if not p.enabled else "Disable")
        self.a_toggle.setToolTip(f"{'启用' if not p.enabled else '停用'}（用户 {self._uid}，pm {'enable' if not p.enabled else 'disable-user'}）")
        self._render_overview(d)
        self._render_perms(d)
        self.c_table.set_rows([])
        self.dump.set_text("")
        self._loaded_tabs = {"overview", "perms"}
        self._tab_changed(self.tabs.value())

    def _select(self, p: PackageInfo | None):
        self._pkg = p
        if p is None:
            self._show_detail(None)
            return
        self._token += 1
        tok = self._token
        self.ctx.run(self.ctx.backend.package_detail, self.serial, p.name, self._uid,
                     done=lambda d: tok == self._token and self._show_detail(d),
                     error=lambda e: tok == self._token and self.ctx.report(e, f"无法读取 {p.name}"))

    def _render_overview(self, d: PackageDetail):
        p = d.info
        w = W.QWidget()
        w.setProperty("page", True)
        v = W.QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 20)
        v.setSpacing(16)

        def dash(x):
            return "—" if x in (None, "") else str(x)
        users = "、".join(str(u) + ("（当前）" if u == self._uid else "") for u in d.installed_users) or "—"
        v.addWidget(ui.card("应用信息", ui.PropertyList([
            ("版本", dash(p.version_name)), ("versionCode", dash(p.version_code)),
            ("targetSdk / minSdk", f"{dash(p.target_sdk)} / {dash(d.min_sdk)}"), ("UID", dash(p.uid)),
            ("已安装于用户", users), ("APK 路径", dash(p.apk_path)), ("数据目录", dash(d.data_dir)),
            ("安装来源", dash(d.installer)), ("首次安装", dash(d.first_install)), ("最近更新", dash(d.last_update)),
        ])))
        v.addStretch(1)
        self.ov_scroll.setWidget(w)

    def _render_perms(self, d: PackageDetail):
        w = W.QWidget()
        w.setProperty("page", True)
        v = W.QVBoxLayout(w)
        v.setContentsMargins(20, 16, 20, 20)
        v.setSpacing(16)
        runtime = [x for x in d.permissions if x.runtime]
        others = [x for x in d.permissions if not x.runtime]
        if runtime:
            box = W.QWidget()
            bl = W.QVBoxLayout(box)
            bl.setContentsMargins(16, 12, 16, 12)
            bl.setSpacing(10)
            for perm in runtime:
                sw = ui.Switch(perm.name.rsplit(".", 1)[-1], perm.granted)
                sw.setToolTip(perm.name)
                sw.toggled.connect(lambda on, perm=perm, sw=sw: self._perm(perm.name, on, sw))
                bl.addWidget(sw)
            v.addWidget(ui.card(f"运行时权限（用户 {self._uid}）· {sum(x.granted for x in runtime)}/{len(runtime)} 已授予", box))
        if others:
            txt = label("\n".join(x.name for x in others), "muted", mono=True, select=True)
            txt.setContentsMargins(16, 12, 16, 12)
            v.addWidget(ui.card(f"安装时权限 · {len(others)}", txt))
        if not d.permissions:
            v.addWidget(label("这个应用没有申请权限。", "faint"))
        v.addStretch(1)
        self.pm_scroll.setWidget(w)

    def _tab_changed(self, key: str):
        if self._detail is None or key in self._loaded_tabs:
            return
        self._loaded_tabs.add(key)
        name, tok = self._detail.info.name, self._token
        if key == "components":
            self.ctx.run(self.ctx.backend.package_components, self.serial, name, self._uid,
                         done=lambda rows: tok == self._token and self._got_components(rows),
                         error=lambda e: self.ctx.report(e))
        elif key == "dump":
            self.dump.set_text("正在读取 dumpsys package …")
            self.ctx.run(self.ctx.backend.dump_package, self.serial, name,
                         done=lambda t: tok == self._token and self.dump.set_text(t),
                         error=lambda e: tok == self._token and self.dump.set_text(f"读取失败：{e}"))

    def _got_components(self, rows: list[Component]):
        self.c_table.set_rows(rows, keep=lambda c: c.name)
        self._apply_comp()

    def _apply_comp(self, *_):
        k = self.c_kind.currentData()
        self.c_table.filter.set_predicate(None if k == "all" else (lambda c: c.kind == k))
        self.c_table.filter.set_text(self.c_filter.text())
        self.c_filter.set_count(self.c_table.filter.rowCount(), self.c_table.table_model.rowCount())

    # 操作 ------------------------------------------------------------
    def _simple(self, method: str, ok: str):
        p = self._detail.info if self._detail else None
        if p:
            self.ctx.run(getattr(self.ctx.backend, method), self.serial, p.name, self._uid,
                         done=lambda _: self.ctx.notify("ok", ok, f"{p.name}（用户 {self._uid}）"))

    def _perm(self, perm: str, on: bool, sw):
        def fail(e):
            sw.blockSignals(True)
            sw.setChecked(not on)
            sw.blockSignals(False)
            self.ctx.report(e, f"无法{'授予' if on else '撤销'} {perm.rsplit('.', 1)[-1]}")
        self.ctx.run(self.ctx.backend.set_permission, self.serial, self._detail.info.name, perm, on, self._uid, error=fail)

    def _set_components(self, enabled: bool):
        rows = self.c_table.selected_rows()
        if not rows or self._detail is None:
            self.ctx.notify("info", "先在组件列表中选中要操作的组件")
            return
        pkg = self._detail.info.name
        verb = "启用" if enabled else "停用"
        if not enabled and not ui.confirm(self, f"停用 {len(rows)} 个组件？", "将执行 <code>pm disable --user "
                                          f"{self._uid} {pkg}/&lt;类名&gt;</code>：<br><code>" + "<br>".join(c.name for c in rows[:8]) +
                                          "</code><br>停用系统组件可能导致相关功能异常，可随时重新启用。", "停用", danger=True):
            return
        b, serial, uid = self.ctx.backend, self.serial, self._uid

        def work():
            for c in rows:
                b.set_component_enabled(serial, f"{pkg}/{c.name}", enabled, uid)

        def done(_):
            self.ctx.notify("ok", f"已{verb} {len(rows)} 个组件")
            self._loaded_tabs.discard("components")
            self._tab_changed("components")
        self.ctx.run(work, done=done)

    def _toggle(self):
        p = self._detail.info if self._detail else None
        if p is None:
            return
        if p.enabled and p.system and not ui.confirm(
                self, f"停用 {p.name}？", f"将对用户 {self._uid} 执行 <code>pm disable-user</code>。停用系统应用可能导致相关功能不可用。可以随时重新启用。",
                "停用", danger=True):
            return
        self.ctx.run(self.ctx.backend.set_enabled, self.serial, p.name, not p.enabled, self._uid,
                     done=lambda _: (self.ctx.notify("ok", ("已启用 " if not p.enabled else "已停用 ") + p.name), self.refresh()))

    def _extract(self):
        p = self._detail.info if self._detail else None
        if p is None:
            return
        dest = self.ctx.settings.export_dir() / "apk"
        dest.mkdir(parents=True, exist_ok=True)
        self.strip.run(f"正在导出 {p.name}", self.ctx.backend.extract_apk, self.serial, p.name, str(dest), cancellable=False,
                       done=lambda paths: (self.ctx.notify("ok", f"已导出 {len(paths)} 个 APK", str(dest)), reveal(paths[0]) if paths else None))

    def _danger(self, action: str):
        p = self._detail.info if self._detail else None
        if p is None:
            return
        b, s, uid = self.ctx.backend, self.serial, self._uid
        if action == "clear":
            if ui.confirm(self, "清除应用数据？", f"将删除 <code>{p.name}</code> 在用户 {uid} 下的全部数据、缓存与登录状态"
                          f"（<code>pm clear --user {uid}</code>）。此操作无法撤销。", "清除数据", danger=True):
                self.ctx.run(b.clear_data, s, p.name, uid, done=lambda _: self.ctx.notify("ok", "已清除数据", p.name))
        elif action == "uninstall_user":
            extra = "系统应用只是对该用户隐藏，可用 <code>pm install-existing</code> 恢复。" if p.system else "其它用户下的安装不受影响。"
            if ui.confirm(self, f"为用户 {uid} 卸载 {p.name}？", f"执行 <code>pm uninstall --user {uid}</code>。{extra}", "卸载", danger=True):
                self.ctx.run(b.uninstall, s, p.name, False, uid, done=lambda _: (self.ctx.notify("ok", f"已为用户 {uid} 卸载", p.name), self.refresh()))
        elif action == "uninstall":
            if p.system:
                self.ctx.notify("warn", "系统应用无法完全卸载", "可以“为当前用户卸载”或停用。")
                return
            if ui.confirm(self, f"卸载 {p.name}？", "将从所有用户中卸载，数据一并删除。", "卸载", danger=True):
                self.ctx.run(b.uninstall, s, p.name, False, None, done=lambda _: (self.ctx.notify("ok", "已卸载", p.name), self.refresh()))

    def _save_dump(self):
        if not self.dump.plain().strip() or self._detail is None:
            return
        name = f"dumpsys-package-{self._detail.info.name}.txt"
        path, _ = W.QFileDialog.getSaveFileName(self, "保存", str(self.ctx.settings.export_dir() / name), "文本文件 (*.txt)")
        if path:
            Path(path).write_text(self.dump.plain(), "utf-8")
            self.ctx.notify("ok", "已保存", path)

    # 批量 dump ------------------------------------------------------
    def _batch_dump(self):
        if self.device is None or self.strip.busy:
            return
        rows = self.table.table_model.rows
        visible = [self.table._row(self.table.filter.index(i, 0)) for i in range(self.table.filter.rowCount())]
        d = Dialog(self, "批量 dump 包信息", "开始")
        d.add_text(f"把用户 {self._uid} 下的包逐个执行 <code>dumpsys package</code>，每个包保存为一个 txt。")
        scope = d.add(ui.SegmentedControl([("third", f"第三方（{sum(1 for p in rows if not p.system)}）"),
                                           ("all", f"全部（{len(rows)}）"), ("visible", f"当前列表（{len(visible)}）")], "third", label="范围"))
        if not d.run():
            return
        pkgs = {"third": [p.name for p in rows if not p.system], "all": [p.name for p in rows],
                "visible": [p.name for p in visible if p]}[scope.value()]
        if not pkgs:
            return
        safe = re.sub(r"[^\w.-]", "_", self.serial)
        dest = self.ctx.settings.export_dir() / f"dumpsys-package-{safe}-user{self._uid}"
        b, serial = self.ctx.backend, self.serial

        def work(progress, cancel: threading.Event):
            dest.mkdir(parents=True, exist_ok=True)
            for i, name in enumerate(pkgs):
                if cancel.is_set():
                    raise Cancelled("已取消")
                progress(Progress(int(i * 100 / len(pkgs)), f"{i + 1}/{len(pkgs)}  {name}"))
                (dest / f"{name}.txt").write_text(b.dump_package(serial, name), "utf-8")
            return str(dest)
        self.strip.run(f"正在 dump {len(pkgs)} 个包", work, done=lambda p: (self.ctx.notify("ok", f"已导出 {len(pkgs)} 个包", p), reveal(p)))

    # 拖放安装 --------------------------------------------------------
    def dragEnterEvent(self, e):
        if self.device and e.mimeData().hasUrls() and all(u.toLocalFile().lower().endswith(".apk") for u in e.mimeData().urls()):
            e.acceptProposedAction()

    def dropEvent(self, e):
        install_apks(self, self.serial, self.strip, [u.toLocalFile() for u in e.mimeData().urls()], done=self.refresh,
                     users=self._users, current_user=self._uid)
