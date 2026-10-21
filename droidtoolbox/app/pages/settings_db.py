"""系统设置：adb shell settings 数据库。

标签：system / secure / global 浏览、编辑、新增、删除；“常用”列出预设的常用键（当前值 + 备注），双击或右键修改。
快照对比三个命名空间一起记录。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

import tbui as ui
from tbui._qt import QtWidgets, Qt

from ..ui.common import BusyBar, Column, DataTable, Dialog, grow, narrow_select, tbtn
from ..ui.snapshot import SnapshotBar
from .base import Page

W = QtWidgets
NAMESPACES = [("system", "system"), ("secure", "secure"), ("global", "global")]
TABS = NAMESPACES + [("presets", "presets")]   # presets：常用键 + 备注
# 改错会断开调试或影响开机流程的键
SENSITIVE = {
    "adb_enabled": "设为 0 会立即关闭 USB 调试，SysDroidPro 将失去与设备的连接。",
    "adb_wifi_enabled": "会影响无线调试连接。",
    "development_settings_enabled": "设为 0 会隐藏开发者选项并可能关闭 USB 调试。",
    "device_provisioned": "设为 0 可能让系统认为设备未完成初始化，部分功能被限制。",
    "user_setup_complete": "设为 0 下次解锁可能重新进入开机向导。",
    "package_verifier_enable": "影响应用安装校验。",
}


@dataclass(frozen=True)
class Preset:
    """“常用”标签的一行：一个键。options 为可选值 [(值, 说明)]，编辑时作为下拉候选（也可以手填其它值）。"""
    ns: str
    key: str
    remark: str
    options: tuple = ()

    @property
    def hint(self) -> str:
        return " / ".join(f"{v} {t}" for v, t in self.options)


_ANIM = (("0", "关闭"), ("0.5", "0.5x"), ("1.0", "1x"), ("2.0", "2x"), ("5.0", "5x"), ("10.0", "10x"))
_ONOFF = (("0", "关闭"), ("1", "开启"))
PRESETS = [
    Preset("global", "window_animation_scale", "窗口动画缩放；调试界面时设 0 关闭动画，操作更快", _ANIM),
    Preset("global", "transition_animation_scale", "过渡动画缩放；与上一项一起设 0 可消除切换动画", _ANIM),
    Preset("global", "animator_duration_scale", "Animator 时长缩放；设 0 时属性动画直接跳到结束状态", _ANIM),
    Preset("system", "show_touches", "在屏幕上显示触摸点（录屏、复现问题时有用）", _ONOFF),
    Preset("system", "pointer_location", "顶部显示触摸坐标与轨迹，排查触摸 / 输入问题", _ONOFF),
    Preset("global", "verifier_verify_adb_installs", "Play Protect 校验 adb 安装的应用；设 0 减少 adb install 弹框",
           (("0", "不校验"), ("1", "校验"))),
    Preset("global", "stay_on_while_plugged_in", "充电时保持亮屏；位掩码 1=AC 2=USB 4=无线，7 为全部",
           (("0", "关闭"), ("2", "USB"), ("3", "AC+USB"), ("7", "全部"))),
    Preset("system", "screen_off_timeout", "无操作灭屏时间（毫秒）",
           (("15000", "15 秒"), ("60000", "1 分钟"), ("600000", "10 分钟"), ("1800000", "30 分钟"), ("2147483647", "永不"))),
    Preset("system", "screen_brightness_mode", "自动亮度", (("0", "手动"), ("1", "自动"))),
    Preset("system", "screen_brightness", "手动亮度（0–255，部分设备范围不同）", (("10", "很暗"), ("102", "中"), ("255", "最亮"))),
    Preset("system", "accelerometer_rotation", "自动旋转", _ONOFF),
    Preset("system", "font_scale", "系统字体缩放", (("0.85", "小"), ("1.0", "默认"), ("1.15", "大"), ("1.3", "最大"))),
    Preset("global", "development_settings_enabled", "开发者选项开关；设 0 会隐藏开发者选项并可能关闭 USB 调试", _ONOFF),
    Preset("global", "adb_enabled", "USB 调试开关；设 0 会立即断开 adb", _ONOFF),
]


class SettingsDbPage(Page):
    id = "settings_db"
    title = "系统设置"
    icon = "settings"

    def build(self):
        lay = W.QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        tb = ui.Toolbar()
        refresh = tb.add(ui.icon_button("refresh-cw", "重新读取（F5）"))
        tb.separator()
        self.ns = tb.add(ui.SegmentedControl(TABS, "global", mono=True, label="命名空间"))
        self.ns.setToolTip("settings 命名空间；presets 为常用键（带备注）")
        self.user = tb.add(narrow_select([("0", "u0", "用户 0")], "0", 64, "Android 用户：system / secure 按用户区分，global 对所有用户生效"))
        self.filter = tb.add(grow(ui.FilterField("Filter: key / value / note", width=240), 160))
        self.snap = tb.add(SnapshotBar(self.ctx, "settings", self._fetch_all, noun="设置项"))
        self.copy = tb.add(ui.icon_button("copy", "复制所选（key=value）"))
        self.export = tb.add(ui.icon_button("download", "导出当前命名空间"))
        self.add = tb.add(tbtn("Add", "新增设置项（settings put）", icon="plus"))
        lay.addWidget(tb)
        self.busy = BusyBar()
        lay.addWidget(self.busy)

        self.stack = W.QStackedWidget()
        self.table = DataTable([
            Column("键", lambda r: r[0], 360, mono=True, tone=lambda r: "warn" if r[0] in SENSITIVE else None,
                   tip=lambda r: SENSITIVE.get(r[0], "")),
            Column("值", lambda r: r[1], None, mono=True, tip=lambda r: r[1],
                   tone=lambda r: "ink-faint" if r[1] == "null" else None),
        ], multi=True)
        self.table.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.stack.addWidget(self.table)
        self.stack.addWidget(self._build_presets())
        lay.addWidget(self.stack, 1)

        refresh.clicked.connect(self.refresh)
        self.ns.changed.connect(lambda *_: self._tab_changed())
        self.user.currentIndexChanged.connect(lambda *_: self.refresh())
        self.filter.textChanged.connect(self._apply)
        self.table.rowActivated.connect(lambda r: self._edit(r[0], r[1]))
        self.table.customContextMenuRequested.connect(self._menu)
        self.copy.clicked.connect(self._copy)
        self.export.clicked.connect(self._export)
        self.add.clicked.connect(lambda: self._edit())
        self._data: dict[str, str] = {}
        self._token = 0

    # 常用 ------------------------------------------------------------
    def _build_presets(self) -> W.QWidget:
        self.ptable = DataTable([
            Column("ns", lambda r: r[0].ns, 72, mono=True, tone=lambda r: "ink-muted", tip=lambda r: "命名空间"),
            Column("键", lambda r: r[0].key, 300, mono=True, tone=lambda r: "warn" if r[0].key in SENSITIVE else None),
            Column("值", lambda r: r[1], 120, mono=True, tone=lambda r: "ink-faint" if r[1] in (None, "null") else None,
                   tip=lambda r: r[1] or "（未设置）"),
            Column("备注", lambda r: r[0].remark, None, tone=lambda r: "ink-muted",
                   tip=lambda r: r[0].remark + (f"\n可选：{r[0].hint}" if r[0].options else "")),
        ], sortable=False)
        self.ptable.rowActivated.connect(lambda r: self._edit_preset(r[0], r[1]))
        self.ptable.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.ptable.customContextMenuRequested.connect(self._preset_menu)
        return self.ptable

    def _load_presets(self):
        if self.device is None:
            return
        serial, user, b = self.serial, self._preset_user(), self.ctx.backend
        self.busy.push()

        def work():
            return {"global": b.list_settings(serial, "global", None), "system": b.list_settings(serial, "system", user),
                    "secure": b.list_settings(serial, "secure", user)}

        def done(data):
            self.busy.pop()
            self.ptable.set_rows([(p, data.get(p.ns, {}).get(p.key)) for p in PRESETS], keep=lambda r: r[0].key)
            self._apply()

        def fail(e):
            self.busy.pop()
            self.ctx.report(e, "无法读取常用设置")
        self.ctx.run(work, done=done, error=fail)

    def _put_preset(self, p: Preset, value: str):
        user = None if p.ns == "global" else self._preset_user()
        self.ctx.run(self.ctx.backend.put_setting, self.serial, p.ns, p.key, value, user,
                     done=lambda _: (self.ctx.notify("ok", "已修改", f"{p.ns} {p.key} = {value}"), self._load_presets()),
                     error=lambda e: self.ctx.report(e, f"无法修改 {p.key}"))

    def _confirm_sensitive(self, key: str, value: str) -> bool:
        return key not in SENSITIVE or ui.confirm(self, f"修改 {key}？", SENSITIVE[key] + f"<br>新值：<code>{value}</code>",
                                                  "仍然修改", danger=True)

    def _edit_preset(self, p: Preset, current: str | None):
        d = Dialog(self, "修改常用设置", "保存")
        user = None if p.ns == "global" else self._preset_user()
        d.add_text(f"<code>settings{'' if user is None else f' --user {user}'} put {p.ns} {p.key} &lt;值&gt;</code><br>{p.remark}")
        box = W.QComboBox()
        box.setEditable(True)
        box.setFont(ui.theme().mono_font(12))
        for v, t in p.options:
            box.addItem(f"{v}    {t}", v)
        box.setEditText(current or "")
        box.activated.connect(lambda i: box.setEditText(box.itemData(i)))
        d.add(box)
        d.add_text("从下拉选择候选值，或直接输入其它值。")
        box.setFocus()
        if not d.run():
            return
        text = box.currentText().strip()
        value = text.split()[0] if text else ""
        if value != (current or "") and self._confirm_sensitive(p.key, value):
            self._put_preset(p, value)

    def _preset_menu(self, pos):
        r = self.ptable.current_row()
        if r is None:
            return
        p = r[0]
        items = [("edit", "修改…", "pencil")] + [(f"set:{v}", f"设为 {v}（{t}）", None) for v, t in p.options] + \
                [None, ("copy", "复制 key=value", "copy")]
        a = ui.menu(items, self).exec(self.ptable.viewport().mapToGlobal(pos))
        if a is None:
            return
        if a.data() == "edit":
            self._edit_preset(p, r[1])
        elif a.data() == "copy":
            QtWidgets.QApplication.clipboard().setText(f"{p.key}={r[1] or ''}")
        elif self._confirm_sensitive(p.key, a.data()[4:]):
            self._put_preset(p, a.data()[4:])

    def _preset_user(self) -> int:
        return int(self.user.currentData() or 0)

    # 标签与加载 ------------------------------------------------------
    @property
    def _user(self) -> int | None:
        return None if self.ns.value() in ("global", "presets") else int(self.user.currentData() or 0)

    def _tab_changed(self):
        presets = self.ns.value() == "presets"
        self.stack.setCurrentIndex(1 if presets else 0)
        for w in (self.export, self.add):
            w.setVisible(not presets)
        self.user.setVisible(self.ns.value() != "global")
        self.refresh()
        self.ctx.window.update_status(self)

    def load(self, device):
        self.snap.set_serial(device.serial)
        self.ctx.run(self.ctx.backend.list_users, device.serial, done=self._got_users, error=lambda e: None)
        self._tab_changed()

    def unload(self):
        self.table.set_rows([])
        self._data = {}
        self.snap.set_serial("")

    def refresh(self):
        if self.ns.value() == "presets":
            self._load_presets()
        else:
            self._load_table()

    def _got_users(self, users):
        keep = self.user.currentData()
        self.user.blockSignals(True)
        self.user.clear()
        for u in users:
            self.user.addItem(f"u{u.id}", str(u.id))
            self.user.setItemData(self.user.count() - 1, f"用户 {u.id} · {u.name}（{u.kind}）", Qt.ItemDataRole.ToolTipRole)
        self.user.setCurrentIndex(max(0, self.user.findData(keep)))
        self.user.setEnabled(len(users) > 1)
        self.user.blockSignals(False)

    def _load_table(self):
        if self.device is None:
            return
        ns, user, serial = self.ns.value(), self._user, self.serial
        self._token += 1
        tok = self._token
        self.busy.push()

        def done(data):
            self.busy.pop()
            if tok != self._token:
                return
            self._data = data
            self.table.set_rows(sorted(data.items()), keep=lambda r: r[0])
            self._apply()

        def fail(e):
            self.busy.pop()
            self.ctx.report(e, f"无法读取 settings {ns}")
        self.ctx.run(self.ctx.backend.list_settings, serial, ns, user, done=done, error=fail)

    def _cur_table(self) -> DataTable:
        return self.ptable if self.ns.value() == "presets" else self.table

    def _apply(self, *_):
        t = self._cur_table()
        t.filter.set_text(self.filter.text())
        self.filter.set_count(t.filter.rowCount(), t.table_model.rowCount())
        self.ctx.window.update_status(self)

    def status_text(self) -> str:
        if self.ns.value() == "presets":
            return f"presets · {self.ptable.filter.rowCount()}/{len(PRESETS)} · u{self._preset_user()}"
        u = "" if self._user is None else f" · u{self._user}"
        return f"{self.ns.value()}{u} · {self.table.filter.rowCount()}/{len(self._data)}"

    def _fetch_all(self, serial: str) -> dict[str, str]:
        """快照用：三个命名空间合并成 “命名空间/键”（在工作线程调用）。"""
        user = int(self.user.currentData() or 0)
        out = {}
        for ns, _ in NAMESPACES:
            for k, v in self.ctx.backend.list_settings(serial, ns, None if ns == "global" else user).items():
                out[f"{ns}/{k}"] = v
        return out

    # 修改 ------------------------------------------------------------
    def _put(self, ns, key, value):
        user = None if ns == "global" else int(self.user.currentData() or 0)
        self.ctx.run(self.ctx.backend.put_setting, self.serial, ns, key, value, user,
                     done=lambda _: (self.ctx.notify("ok", "已修改", f"{ns} {key} = {value}"), self._load_table()),
                     error=lambda e: self.ctx.report(e, f"无法修改 {key}"))

    def _edit(self, key: str = "", value: str = ""):
        ns = self.ns.value()
        user = self._user
        d = Dialog(self, "新增设置项" if not key else "修改设置项", "保存")
        d.add_text(f"命名空间 <b>{ns}</b>" + ("" if user is None else f"，用户 <b>{user}</b>") +
                   f"。等同于 <code>settings{'' if user is None else f' --user {user}'} put {ns} &lt;键&gt; &lt;值&gt;</code>")
        k = d.add(ui.TextField("键", "例如 stay_on_while_plugged_in", mono=True))
        v = d.add(ui.TextField("值", "", mono=True))
        k.edit.setText(key)
        k.edit.setReadOnly(bool(key))
        v.edit.setText(value)
        warn = d.add_text("")
        warn.setVisible(False)

        def upd(*_):
            msg = SENSITIVE.get(k.edit.text().strip())
            warn.setText(f"<b>注意：</b>{msg}" if msg else "")
            warn.setVisible(bool(msg))
        k.edit.textChanged.connect(upd)
        upd()
        (v.edit if key else k.edit).setFocus()

        def validate():
            name = k.edit.text().strip()
            err = None if name and " " not in name else "键不能为空或包含空格"
            k.set_error(err)
            return err
        d.validate = validate
        if not d.run():
            return
        key, value = k.edit.text().strip(), v.edit.text()
        if key in SENSITIVE and not ui.confirm(self, f"修改 {key}？", SENSITIVE[key] + f"<br>新值：<code>{value}</code>", "仍然修改", danger=True):
            return
        self._put(ns, key, value)

    def _delete(self, rows):
        if not rows:
            return
        ns, user = self.ns.value(), self._user
        names = "<br>".join(f"<code>{r[0]}</code>" for r in rows[:10]) + ("<br>…" if len(rows) > 10 else "")
        if not ui.confirm(self, f"删除 {len(rows)} 个设置项？", f"将从 {ns} 中删除（settings delete）：<br>{names}", "删除", danger=True):
            return
        b, serial = self.ctx.backend, self.serial

        def work():
            for r in rows:
                b.delete_setting(serial, ns, r[0], user)
        self.ctx.run(work, done=lambda _: (self.ctx.notify("ok", f"已删除 {len(rows)} 项"), self._load_table()))

    def _menu(self, pos):
        rows = self.table.selected_rows()
        if not rows:
            return
        items = [("edit", "修改…", "pencil"), ("copy", "复制 key=value", "copy"), None,
                 ("delete", f"删除 {len(rows)} 项…", "trash-2", None, True)]
        a = ui.menu(items if len(rows) == 1 else items[1:], self).exec(self.table.viewport().mapToGlobal(pos))
        if a is None:
            return
        {"edit": lambda: self._edit(*rows[0]), "copy": self._copy, "delete": lambda: self._delete(rows)}[a.data()]()

    def _copy(self):
        rows = self.table.selected_rows() if self.ns.value() != "presets" else \
            [(p.key, v or "") for p, v in self.ptable.selected_rows()]
        if rows:
            QtWidgets.QApplication.clipboard().setText("\n".join(f"{k}={v}" for k, v in rows))
            self.ctx.notify("ok", f"已复制 {len(rows)} 项")

    def _export(self):
        if not self._data:
            return
        ns = self.ns.value()
        name = f"settings-{ns}-{self.serial.replace(':', '_')}-{time.strftime('%Y%m%d-%H%M%S')}.txt"
        path, _ = W.QFileDialog.getSaveFileName(self, "导出", str(self.ctx.settings.export_dir() / name), "文本文件 (*.txt)")
        if path:
            Path(path).write_text("".join(f"{k}={v}\n" for k, v in sorted(self._data.items())), "utf-8")
            self.ctx.notify("ok", f"已导出 {len(self._data)} 项", path)
