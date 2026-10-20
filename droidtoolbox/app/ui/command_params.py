"""命令参数输入框：按参数类型提供输入控件，实时预览展开后的命令行。

- package / service / init / pid / prop：可输入的下拉框，选项在后台从当前设备读取，输入时按包含匹配筛选
- file / dir：路径输入框 + 浏览按钮（电脑上的文件 / 目录）
- 固定选项：只读下拉框
- text：普通输入框
初始值：上次填写的值 → 参数默认值。
"""
from __future__ import annotations

import re

import tbui as ui
from tbui._qt import QtCore, QtWidgets, Qt

from ..core.commands import Command, Param
from .common import Dialog, hbox, label, open_files, pick_dir, tbtn

W = QtWidgets

# 类型 → (说明, 从后端读取选项的函数 (backend, serial) -> list[str])
LOOKUPS = {
    "package": ("设备上的包名", lambda b, s: sorted(p.name for p in b.list_packages(s, 0))),
    "service": ("Binder 服务", lambda b, s: sorted(x.name for x in b.list_binder_services(s))),
    "init": ("init 服务", lambda b, s: sorted(x.name for x in b.list_init_services(s))),
    "pid": ("进程", lambda b, s: [f"{p.pid}  {p.name}" for p in sorted(b.list_processes(s), key=lambda p: (p.name.startswith("["), p.name))]),   # 内核线程排在后面
    "prop": ("系统属性", lambda b, s: sorted(b.get_props(s))),
}
TYPE_TEXT = {"text": "文本", "file": "电脑上的文件", "dir": "电脑上的目录", **{k: v[0] for k, v in LOOKUPS.items()}}


def param_value(p: Param, text: str) -> str:
    """输入框文字 → 参数值（pid 下拉项是“824  system_server”，只取 PID）。"""
    text = text.strip()
    if p.type == "pid":
        m = re.match(r"\d+", text)
        return m.group(0) if m else text
    return text


class _LookupBox(W.QComboBox):
    """可输入、按包含匹配筛选的下拉框。"""

    def __init__(self, placeholder: str):
        super().__init__()
        self.setEditable(True)
        self.setInsertPolicy(W.QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(16)
        self.lineEdit().setPlaceholderText(placeholder)
        self.lineEdit().setFont(ui.theme().mono_font(12))
        c = W.QCompleter(self.model(), self)
        c.setFilterMode(Qt.MatchFlag.MatchContains)
        c.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        c.setCompletionMode(W.QCompleter.CompletionMode.PopupCompletion)
        self.setCompleter(c)

    def set_items(self, items: list[str]):
        text = self.currentText()
        self.blockSignals(True)
        self.clear()
        self.addItems(items)
        self.setEditText(text)
        self.blockSignals(False)

    def text(self) -> str:
        return self.currentText()


class ParamDialog(Dialog):
    def __init__(self, parent, ctx, cmd: Command, serial: str | None):
        super().__init__(parent, f"执行：{cmd.name}", "执行", width=560)
        self.ctx, self.cmd = ctx, cmd
        self.inputs: dict[str, tuple[Param, W.QWidget]] = {}
        self.errors: dict[str, W.QLabel] = {}
        last = ctx.commands.last_values
        first = None
        for p in cmd.params:
            init = last.get(p.name) or p.default
            head = label(p.name if p.type == "text" and not p.choices else f"{p.name}  ·  {TYPE_TEXT.get(p.type, p.type) if not p.choices else '选项'}")
            ui.repolish(head, fieldlabel=True)
            if p.choices:
                w = W.QComboBox()
                w.addItems(list(p.choices))
                w.setCurrentText(init if init in p.choices else (p.default or p.choices[0]))
                w.currentTextChanged.connect(self._update)
                row = w
            elif p.type in LOOKUPS:
                w = _LookupBox("加载中…" if serial else "未连接设备，可直接输入")
                w.setEditText(init)
                w.editTextChanged.connect(self._update)
                row = w
                if serial:
                    ctx.run(LOOKUPS[p.type][1], ctx.backend, serial,
                            done=lambda items, w=w: self._loaded(w, items),
                            error=lambda e, w=w: w.lineEdit().setPlaceholderText(f"读取失败，可直接输入（{e}）"))
            else:
                w = W.QLineEdit(init)
                w.setFont(ui.theme().mono_font(12))
                w.textChanged.connect(self._update)
                row = w
                if p.type in ("file", "dir"):
                    b = tbtn("Browse", "选择文件" if p.type == "file" else "选择目录", icon="folder-open")
                    b.clicked.connect(lambda _=False, p=p, w=w: self._browse(p, w))
                    row = hbox(w, b, spacing=6)
            err = label("", px=11)
            ui.repolish(err, tone="danger")
            err.hide()
            box = W.QWidget()
            bl = W.QVBoxLayout(box)
            bl.setContentsMargins(0, 0, 0, 0)
            bl.setSpacing(4)
            bl.addWidget(head)
            bl.addWidget(row)
            bl.addWidget(err)
            self.add(box)
            self.inputs[p.name] = (p, w)
            self.errors[p.name] = err
            first = first or w
        self.preview = self.add(label("", "faint", mono=True, px=11, select=True))   # 展开后的命令行
        self.preview.setTextFormat(Qt.TextFormat.PlainText)
        self.preview.setWordWrap(True)
        self.validate = self._validate
        self._update()
        if first is not None:
            QtCore.QTimer.singleShot(0, first.setFocus)

    # 取值 ------------------------------------------------------------
    def values(self) -> dict[str, str]:
        out = {}
        for name, (p, w) in self.inputs.items():
            text = w.currentText() if isinstance(w, W.QComboBox) else w.text()
            out[name] = param_value(p, text)
        return out

    def _update(self, *_):
        filled = {k: v for k, v in self.values().items() if v}      # 没填的参数在预览里保留 {…}
        self.preview.setText("\n".join(self.cmd.lines(filled)))

    def _validate(self):
        bad = None
        for name, v in self.values().items():
            e = None if v else "不能为空"
            self.errors[name].setText(e or "")
            self.errors[name].setVisible(bool(e))
            bad = bad or e
        return bad

    # 内部 ------------------------------------------------------------
    def _loaded(self, w: _LookupBox, items: list[str]):
        w.set_items(items)
        w.lineEdit().setPlaceholderText(f"{len(items)} 项，输入可筛选")

    def _browse(self, p: Param, w: W.QLineEdit):
        if p.type == "file":
            paths = open_files(self, f"选择 {p.name}", start=w.text())
            if paths:
                w.setText(paths[0])
        else:
            d = pick_dir(self, f"选择 {p.name}", w.text())
            if d:
                w.setText(d)


def ask_param_values(parent, ctx, cmd: Command, serial: str | None) -> dict[str, str] | None:
    """没有参数返回 {}；取消返回 None；确定后记住填写的值。"""
    if not cmd.params:
        return {}
    d = ParamDialog(parent, ctx, cmd, serial)
    if not d.run():
        return None
    vals = d.values()
    ctx.commands.remember(vals)
    return vals
