"""快照对比：记录一份键值数据（属性 / settings），稍后与当前值对比，列出新增、删除、变化的键。

快照保存在 data/snapshots/<kind>/<设备序列号>/<时间戳>.json，可命名、保留多个，不做跨设备对比。

    bar = SnapshotBar(ctx, "props", fetch=lambda serial: backend.get_props(serial))
    toolbar.add(bar)
    bar.set_serial(serial)   # 切换设备时调用
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Callable

import tbui as ui
from tbui._qt import QtWidgets, Qt

from ..core.settings import DATA_DIR
from .common import Column, DataTable, Dialog, ask_text, tbtn

W = QtWidgets
CHANGE_TEXT = {"added": ("新增", "ok"), "removed": ("删除", "danger"), "changed": ("变化", "warn")}


def diff(old: dict[str, str], new: dict[str, str]) -> list[tuple[str, str, str | None, str | None]]:
    """返回 [(类型, 键, 快照值, 当前值)]，按键排序。"""
    rows = []
    for k in sorted(set(old) | set(new)):
        a, b = old.get(k), new.get(k)
        if a == b:
            continue
        rows.append(("added" if a is None else "removed" if b is None else "changed", k, a, b))
    return rows


class SnapshotBar(W.QWidget):
    """工具栏里的一组控件：记录快照 / 选择快照 / 对比 / 删除。"""

    def __init__(self, ctx, kind: str, fetch: Callable[[str], dict[str, str]], noun: str = "键"):
        super().__init__()
        self.ctx, self.kind, self.fetch, self.noun = ctx, kind, fetch, noun
        self.serial = ""
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.record = ui.icon_button("camera", "记录快照：保存当前全部值，之后在设备上操作，再点 Diff 对比")
        self.pick = ui.select([], size="sm")
        self.pick.setFixedWidth(120)
        ui.repolish(self.pick, narrow=True)
        self.pick.setToolTip("选择要对比的快照")
        self.compare = tbtn("Diff", "与所选快照对比，列出新增 / 删除 / 变化", width=48)
        self.remove = ui.icon_button("trash-2", "删除所选快照", tone="danger")
        for w in (self.record, self.pick, self.compare, self.remove):
            lay.addWidget(w)
        self.record.clicked.connect(self._record)
        self.compare.clicked.connect(self._compare)
        self.remove.clicked.connect(self._remove)
        self._reload()

    def _dir(self) -> Path:
        return DATA_DIR / "snapshots" / self.kind / re.sub(r"[^\w.-]", "_", self.serial or "none")

    def set_serial(self, serial: str):
        self.serial = serial
        self._reload()

    def _reload(self, select: str | None = None):
        self.pick.clear()
        files = sorted(self._dir().glob("*.json"), reverse=True) if self.serial else []
        for f in files:
            try:
                meta = json.loads(f.read_text("utf-8"))
                self.pick.addItem(f"{meta.get('name') or '快照'}  {meta.get('time', '')[11:16]}", str(f))
                self.pick.setItemData(self.pick.count() - 1, f"{meta.get('name')} · {meta.get('time')}", Qt.ItemDataRole.ToolTipRole)
            except (OSError, ValueError):
                continue
        if not files:
            self.pick.addItem("(no snapshot)", "")
        if select:
            self.pick.setCurrentIndex(max(0, self.pick.findData(select)))
        has = bool(self.pick.currentData())
        self.compare.setEnabled(has)
        self.remove.setEnabled(has)

    def _record(self):
        if not self.serial:
            return
        name = ask_text(self, "记录快照", "快照名称", time.strftime("%H:%M 快照"), hint="记录后在设备上操作，再点“对比”查看变化。",
                        ok_text="记录", mono=False)
        if not name:
            return
        serial = self.serial

        def done(data: dict[str, str]):
            d = self._dir()
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
            path.write_text(json.dumps({"name": name, "serial": serial, "kind": self.kind,
                                        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "data": data}, ensure_ascii=False, indent=1), "utf-8")
            self._reload(select=str(path))
            self.ctx.notify("ok", f"已记录快照“{name}”", f"{len(data)} 个{self.noun}。现在去设备上操作，然后点“对比”。")
        self.ctx.run(self.fetch, serial, done=done)

    def _compare(self):
        path = self.pick.currentData()
        if not path or not self.serial:
            return
        snap = json.loads(Path(path).read_text("utf-8"))

        def done(now: dict[str, str]):
            rows = diff(snap.get("data", {}), now)
            show_diff(self, f"对比：{snap.get('name')}（{snap.get('time', '')[5:16]}）→ 当前", rows, self.noun)
        self.ctx.run(self.fetch, self.serial, done=done)

    def _remove(self):
        path = self.pick.currentData()
        if path and ui.confirm(self, "删除快照？", f"将删除快照“{self.pick.currentText()}”。", "删除", danger=True):
            Path(path).unlink(missing_ok=True)
            self._reload()


def show_diff(parent, title: str, rows: list[tuple[str, str, str | None, str | None]], noun: str = "键"):
    d = Dialog(parent, title, "关闭", width=860)
    d.cancel_btn.hide()
    counts = {k: sum(1 for r in rows if r[0] == k) for k in CHANGE_TEXT}
    if not rows:
        d.add_text(f"没有{noun}发生变化。")
        d.run()
        return
    d.add_text(f"共 {len(rows)} 处变化：新增 {counts['added']}，删除 {counts['removed']}，变化 {counts['changed']}。双击一行复制“键=当前值”。")
    filt = d.add(ui.FilterField(f"过滤{noun}或值", width=300))
    t = DataTable([
        Column("变化", lambda r: CHANGE_TEXT[r[0]][0], 64, tone=lambda r: CHANGE_TEXT[r[0]][1]),
        Column(noun, lambda r: r[1], 300, mono=True, tip=lambda r: r[1]),
        Column("快照值", lambda r: r[2], 200, mono=True, tone=lambda r: "ink-muted", tip=lambda r: r[2] or ""),
        Column("当前值", lambda r: r[3], None, mono=True, tip=lambda r: r[3] or ""),
    ])
    t.set_rows(rows)
    t.setMinimumHeight(360)
    d.add(t)
    filt.textChanged.connect(lambda s: (t.filter.set_text(s), filt.set_count(t.filter.rowCount(), len(rows))))
    t.rowActivated.connect(lambda r: QtWidgets.QApplication.clipboard().setText(f"{r[1]}={r[3] if r[3] is not None else ''}"))
    d.run()
