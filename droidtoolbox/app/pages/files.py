"""文件管理：浏览设备文件系统，上传 / 下载 / 新建 / 重命名 / 删除，支持从资源管理器拖入上传。"""
from __future__ import annotations

import posixpath

import tbui as ui
from tbui._qt import QtGui, QtWidgets, Qt

from ..backend.models import FileEntry
from ..ui.common import BusyBar, Column, DataTable, TaskStrip, ask_text, fmt_size, pick_dir, reveal
from .base import Page

W = QtWidgets
PLACES = [("/sdcard", "内部存储 /sdcard", "smartphone"), ("/sdcard/Download", "下载 /sdcard/Download", "download"),
          ("/sdcard/DCIM", "相机 /sdcard/DCIM", "camera"), ("/data/local/tmp", "临时目录 /data/local/tmp", "folder"),
          None, ("/system", "/system", "hard-drive"), ("/vendor", "/vendor", "hard-drive"), ("/", "根目录 /", "folder")]


def _icon(e: FileEntry) -> str:
    if e.is_link:
        return "link"
    if e.is_dir:
        return "folder"
    n = e.name.lower()
    if n.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
        return "image"
    if n.endswith((".mp4", ".mkv", ".webm")):
        return "video"
    if n.endswith((".apk", ".zip", ".so")):
        return "package"
    if n.endswith((".txt", ".log", ".prop", ".xml", ".json", ".sh", ".cfg", ".rc")):
        return "file-text"
    return "file"


class FilesPage(Page):
    id = "files"
    title = "文件管理"
    icon = "folder"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        self.back = tb.add(ui.icon_button("arrow-left", "后退（Alt+←）"))
        self.up = tb.add(ui.icon_button("arrow-up", "上一级（Backspace）"))
        refresh = tb.add(ui.icon_button("refresh-cw", "刷新（F5）"))
        places = ui.menu(PLACES, self)
        places.triggered.connect(lambda a: self.go(a.data()))
        pb = tb.add(ui.icon_button("hard-drive", "常用位置"))
        pb.setMenu(places)
        pb.setPopupMode(W.QToolButton.ToolButtonPopupMode.InstantPopup)
        self.path = tb.add(W.QLineEdit())
        self.path.setFont(ui.theme().mono_font(12))
        ui.repolish(self.path, path=True)
        self.path.setSizePolicy(W.QSizePolicy.Policy.Expanding, W.QSizePolicy.Policy.Fixed)
        tb.separator()
        mkdir = tb.add(ui.icon_button("folder-plus", "新建文件夹"))
        upload = tb.add(ui.icon_button("upload", "上传到当前目录（也可以把文件拖进窗口）"))
        self.download = tb.add(ui.icon_button("download", "下载所选到本地"))
        self.remove = tb.add(ui.icon_button("trash-2", "删除所选", tone="danger"))
        lay.addWidget(tb)
        self.busy = BusyBar()
        lay.addWidget(self.busy)
        self.strip = TaskStrip((12, 8, 12, 8))
        lay.addWidget(self.strip)
        self.banner_box = W.QWidget()
        self.banner_lay = W.QVBoxLayout(self.banner_box)
        self.banner_lay.setContentsMargins(12, 8, 12, 8)
        self.banner_box.hide()
        lay.addWidget(self.banner_box)

        self.table = DataTable([
            Column("名称", lambda e: e.name + (f"  →  {e.link_target}" if e.link_target else ""), 360, mono=True,
                   icon=_icon, sort=lambda e: (not e.is_dir, e.name.lower())),
            Column("大小", lambda e: "" if e.is_dir else fmt_size(e.size), 96, mono=True, align="right",
                   sort=lambda e: -1 if e.is_dir else (e.size or 0)),
            Column("权限", lambda e: e.mode, 110, mono=True, tone=lambda e: "ink-muted"),
            Column("所有者", lambda e: f"{e.owner or '?'}:{e.group or '?'}", 150, mono=True, tone=lambda e: "ink-muted"),
            Column("修改时间", lambda e: e.mtime, None, mono=True, tone=lambda e: "ink-muted"),
        ], multi=True)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        lay.addWidget(self.table, 1)

        self.back.clicked.connect(self._back)
        self.up.clicked.connect(lambda: self.go(posixpath.dirname(self.cwd.rstrip("/")) or "/"))
        refresh.clicked.connect(self.refresh)
        self.path.returnPressed.connect(lambda: self.go(self.path.text().strip() or "/"))
        mkdir.clicked.connect(self._mkdir)
        upload.clicked.connect(self._upload_pick)
        self.download.clicked.connect(self._download)
        self.remove.clicked.connect(self._delete)
        self.table.rowActivated.connect(self._open)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.selectionChangedRows.connect(self._sel)
        for key, fn in (("Backspace", lambda: self.up.click()), ("Alt+Left", self._back), ("Delete", self._delete), ("F2", self._rename)):
            sc = QtGui.QShortcut(QtGui.QKeySequence(key), self.table)
            sc.setContext(Qt.ShortcutContext.WidgetShortcut)
            sc.activated.connect(fn)
        self.setAcceptDrops(True)
        self.cwd = "/sdcard"
        self._history: list[str] = []
        self._token = 0
        self._sel([])

    # 导航 ------------------------------------------------------------
    def load(self, device):
        self._history.clear()
        self.go(self.cwd, record=False)

    def unload(self):
        self.table.set_rows([])

    def refresh(self):
        self.go(self.cwd, record=False)

    def go(self, path: str, record: bool = True):
        if self.device is None:
            return
        path = posixpath.normpath("/" + path.strip("/")) if path != "/" else "/"
        self.busy.push()
        self._token += 1
        tok = self._token
        prev = self.cwd

        def done(entries):
            self.busy.pop()
            if tok != self._token:
                return
            if record and prev != path:
                self._history.append(prev)
            self.cwd = path
            self.path.setText(path)
            self._hide_banner()
            self.table.set_rows(entries)
            self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
            self.back.setEnabled(bool(self._history))
            self.up.setEnabled(path != "/")
            self.ctx.window.update_status(self)

        def fail(e):
            self.busy.pop()
            if tok != self._token:
                return
            self.path.setText(self.cwd)
            self._show_banner(f"无法打开 {path}", str(e))
        self.ctx.run(self.ctx.backend.list_dir, self.serial, path, done=done, error=fail)

    def _back(self):
        if self._history:
            self.go(self._history.pop(), record=False)

    def _open(self, e: FileEntry):
        if e.is_dir:
            self.go(e.path)
        else:
            self._download([e])

    def status_text(self) -> str:
        rows = self.table.table_model.rows
        dirs = sum(1 for e in rows if e.is_dir)
        return f"{self.cwd} · {dirs} dirs · {len(rows) - dirs} files"

    def _sel(self, rows):
        self.download.setEnabled(bool(rows))
        self.remove.setEnabled(bool(rows))

    # 操作 ------------------------------------------------------------
    def _menu(self, pos):
        rows = self.table.selected_rows()
        if not rows:
            return
        items = [("download", "下载到本地", "download"), ("copy", "复制路径", "copy")]
        if len(rows) == 1:
            items.append(("rename", "重命名（F2）", "pencil"))
        items += [None, ("delete", f"删除 {len(rows)} 项…", "trash-2", "Delete", True)]
        m = ui.menu(items, self)
        a = m.exec(self.table.viewport().mapToGlobal(pos))
        if a is None:
            return
        {"download": lambda: self._download(rows), "rename": self._rename, "delete": self._delete,
         "copy": lambda: QtWidgets.QApplication.clipboard().setText("\n".join(e.path for e in rows))}[a.data()]()

    def _mkdir(self):
        name = ask_text(self, "新建文件夹", "名称", placeholder="new_folder", ok_text="创建",
                        check=lambda s: None if s.strip() and "/" not in s else "名称不能为空或包含 /")
        if name:
            self.ctx.run(self.ctx.backend.mkdir, self.serial, posixpath.join(self.cwd, name), done=lambda _: self.refresh())

    def _rename(self):
        rows = self.table.selected_rows()
        if len(rows) != 1:
            return
        e = rows[0]
        name = ask_text(self, "重命名", "新名称", e.name, ok_text="重命名",
                        check=lambda s: None if s.strip() and "/" not in s else "名称不能为空或包含 /")
        if name and name != e.name:
            self.ctx.run(self.ctx.backend.rename, self.serial, e.path, posixpath.join(self.cwd, name), done=lambda _: self.refresh())

    def _delete(self):
        rows = self.table.selected_rows()
        if not rows:
            return
        names = "<br>".join(f"<code>{e.path}</code>" for e in rows[:8]) + ("<br>…" if len(rows) > 8 else "")
        if ui.confirm(self, f"删除 {len(rows)} 项？", f"将从设备上永久删除（rm -rf）：<br>{names}", "删除", danger=True):
            self.ctx.run(self.ctx.backend.delete, self.serial, [e.path for e in rows],
                         done=lambda _: (self.ctx.notify("ok", f"已删除 {len(rows)} 项"), self.refresh()))

    def _download(self, rows=None):
        rows = rows if isinstance(rows, list) else self.table.selected_rows()
        if not rows or self.strip.busy:
            return
        dest = pick_dir(self, "下载到", str(self.ctx.settings.export_dir()))
        if not dest:
            return
        title = f"正在下载 {rows[0].name}" if len(rows) == 1 else f"正在下载 {len(rows)} 项"
        self.strip.run(title, self.ctx.backend.pull, self.serial, [e.path for e in rows], dest,
                       done=lambda paths: (self.ctx.notify("ok", f"已下载 {len(paths)} 项", dest), paths and reveal(paths[0])))

    def _upload_pick(self):
        paths, _ = W.QFileDialog.getOpenFileNames(self, f"上传到 {self.cwd}", "", "所有文件 (*)")
        if paths:
            self._upload(paths)

    def _upload(self, paths: list[str]):
        if self.strip.busy:
            self.ctx.notify("info", "已有传输在进行")
            return
        import os
        title = f"正在上传 {os.path.basename(paths[0])}" if len(paths) == 1 else f"正在上传 {len(paths)} 项"
        cwd = self.cwd
        self.strip.run(title + f" → {cwd}", self.ctx.backend.push, self.serial, paths, cwd,
                       done=lambda _: (self.ctx.notify("ok", f"已上传 {len(paths)} 项", cwd), self.refresh()))

    def dragEnterEvent(self, e):
        if self.device and e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self._upload(paths)

    # 提示条 ----------------------------------------------------------
    def _show_banner(self, title: str, body: str):
        self._hide_banner()
        b = ui.Banner("warn", title, body, dismissible=True)
        b.dismissed.connect(self.banner_box.hide)
        self.banner_lay.addWidget(b)
        self.banner_box.show()

    def _hide_banner(self):
        while self.banner_lay.count():
            w = self.banner_lay.takeAt(0).widget()
            if w:
                w.deleteLater()
        self.banner_box.hide()
