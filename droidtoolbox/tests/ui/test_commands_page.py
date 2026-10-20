"""命令库页：草稿 / 未保存提示、参数插入与校验、分组管理、参数输入框、root 检查、历史持久化与重新执行、复制命令行。"""
from __future__ import annotations

import pytest

from app.backend.mock import MockBackend
from tests.conftest import pump, wait_until

pytestmark = pytest.mark.ui
SERIAL = "3A281FDH2000XY"     # Mock 设备：userdebug，初始未 root


@pytest.fixture
def env(qapp, tmp_path, monkeypatch):
    import tbui
    from app.core.commands import CommandStore
    from app.core.settings import Settings
    from app.core.state import AppState
    from app.main_window import MainWindow
    monkeypatch.setattr(tbui, "confirm", lambda *a, **k: True)
    s = Settings(tmp_path / "s.ini")
    s.set("auto_root", False)
    s.set("last_page", "commands")
    st = AppState(MockBackend(), s, CommandStore(tmp_path / "c.json", tmp_path / "h.json"))
    w = MainWindow(st)
    w.show()
    st.start()
    assert wait_until(qapp, lambda: any(d.serial == SERIAL for d in st.devices), 5)
    st.select(SERIAL)
    w.open_page("commands")
    pump(qapp, 0.3)
    yield w, w.pages["commands"], st.commands, tmp_path
    cp = w.pages["commands"]
    cp._clean = cp._snapshot()      # 关闭时不弹“保存修改？”
    cp._draft = False
    w.close()
    pump(qapp, 0.2)


def _choice(monkeypatch, answer):
    import app.pages.adb_commands as ac
    asked = []
    monkeypatch.setattr(ac, "ask_choice", lambda *a, **k: (asked.append(a[1]), answer)[1])
    return asked


def _new_saved(cp, name, script, group="我的命令"):
    cp._new(group)
    cp.f_name.edit.setText(name)
    cp.f_script.setPlainText(script)
    assert cp._save()
    return cp._cur


def test_new_is_draft_until_saved(env):
    w, cp, store, _ = env
    n = len(store.user)
    cp._new()
    assert cp._draft and len(store.user) == n, "新建只是草稿，不写入命令库"
    assert cp.state_badge.text() == "new · unsaved" and cp.save_btn.isEnabled()
    assert not cp.del_btn.isEnabled()
    cp.f_name.edit.setText("adbd 状态")
    cp.f_script.setPlainText("getprop init.svc.adbd")
    assert cp._save()
    assert len(store.user) == n + 1 and not cp._draft
    assert cp.tree.currentItem().text(0) == "adbd 状态"
    assert not cp.save_btn.isEnabled() and not cp.state_badge.isVisible()


def test_switch_with_unsaved_changes_asks(env, monkeypatch):
    w, cp, store, _ = env
    c = _new_saved(cp, "A", "getprop ro.build.type")
    cp.f_script.setPlainText("getprop ro.build.tags")
    assert cp._dirty() and cp.state_badge.text() == "unsaved"
    asked = _choice(monkeypatch, None)                     # 取消：留在当前命令，修改保留
    assert not cp._select("builtin:当前焦点窗口")
    assert asked and cp._cur.id == c.id and cp.f_script.toPlainText() == "getprop ro.build.tags"
    _choice(monkeypatch, "discard")                        # 放弃：切换，命令库不变
    assert cp._select("builtin:当前焦点窗口")
    assert store.get(c.id).script == "getprop ro.build.type"
    cp._select(c.id)
    cp.f_script.setPlainText("getprop ro.build.tags")
    _choice(monkeypatch, "save")                           # 保存：写入后切换
    assert cp._select("builtin:当前焦点窗口")
    assert store.get(c.id).script == "getprop ro.build.tags"


def test_insert_param_and_problems_block_save(env):
    w, cp, store, _ = env
    cp._new()
    cp.f_name.edit.setText("坏命令")
    cp.f_script.setPlainText("dumpsys meminfo ")
    cur = cp.f_script.textCursor()
    cur.movePosition(cur.MoveOperation.End)
    cp.f_script.setTextCursor(cur)
    cp._insert_param("{包名:package}", "包名")
    assert cp.f_script.toPlainText() == "dumpsys meminfo {包名:package}"
    assert cp.f_script.textCursor().selectedText() == "包名"
    assert "设备上的包名" in cp.params_lbl.text()
    cp.f_script.setPlainText("echo {x:abc}")
    assert cp.problems_lbl.isVisibleTo(cp) and "abc" in cp.problems_lbl.text()
    n = len(store.user)
    assert not cp._save() and len(store.user) == n


def test_group_move_rename_delete(env, monkeypatch):
    import app.pages.adb_commands as ac
    w, cp, store, _ = env
    a = _new_saved(cp, "a", "echo a", "音频")
    b = _new_saved(cp, "b", "echo b", "音频")
    cp._move_to_group(a, "显示")
    assert store.get(a.id).group == "显示" and store.get(b.id).group == "音频"
    monkeypatch.setattr(ac, "ask_text", lambda *a, **k: "音频 HAL")
    cp._rename_group("音频")
    assert store.get(b.id).group == "音频 HAL"
    assert cp._check_group_name("系统信息") == "与内置分组同名"
    cp._delete_group("显示")
    assert store.get(a.id) is None and store.get(b.id) is not None


def test_param_dialog_lookups_and_values(env, qapp):
    from app.ui.command_params import ParamDialog
    w, cp, store, _ = env
    cp._new()
    cp.f_name.edit.setText("p")
    cp.f_script.setPlainText("cat /proc/{PID:pid}/status\nlogcat -b {缓冲区|main|crash} -d | tail -n {行数=50}\nls {目录:dir}")
    d = ParamDialog(w, w.ctx, cp._editing(), SERIAL)
    _, pid_box = d.inputs["PID"]
    assert wait_until(qapp, lambda: pid_box.count() > 0, 5), "PID 下拉应从设备进程列表加载"
    item = next(pid_box.itemText(i) for i in range(pid_box.count()) if "system_server" in pid_box.itemText(i))
    pid_box.setEditText(item)
    vals = d.values()
    assert vals["PID"] == item.split()[0] and vals["缓冲区"] == "main" and vals["行数"] == "50"
    assert d._validate() == "不能为空"                     # 目录没填
    d.inputs["目录"][1].setText("C:/tmp")
    assert d._validate() is None
    assert d.preview.text().splitlines()[1] == "logcat -b main -d | tail -n 50"
    d.reject()


def test_root_command_offers_adb_root(env, qapp, monkeypatch):
    import app.pages.adb_commands as ac
    w, cp, store, _ = env
    b = w.state.backend
    assert not b._st(SERIAL)["root"]
    c = _new_saved(cp, "要 root", "getprop ro.build.type")
    cp.f_root.setChecked(True)
    assert cp._save()
    asked = _choice(monkeypatch, "root")
    monkeypatch.setattr(ac, "ask_param_values", lambda *a, **k: {})
    cp._run()
    assert wait_until(qapp, lambda: not cp._running and "exit" in cp.status.text(), 10), cp.status.text()
    assert asked == ["需要 root"] and b._st(SERIAL)["root"], "选择 adb root 后应先 root 再执行"
    assert store.history[0].command_id == c.id


def test_history_persists_and_rerun(env, qapp):
    from app.core.commands import CommandStore
    w, cp, store, tmp = env
    cp._select("builtin:构建信息")
    cp._run()
    assert wait_until(qapp, lambda: not cp._running and "exit" in cp.status.text(), 5)
    reloaded = CommandStore(tmp / "c.json", tmp / "h.json")
    assert reloaded.history and reloaded.history[0].command == "构建信息" and reloaded.history[0].kind == "shell"
    cp.result.set_text("")
    cp._show_history(1)
    assert "历史记录" in cp.result.plain() and cp.rerun_btn.isEnabled()
    n = len(store.history)
    cp._rerun()
    assert wait_until(qapp, lambda: not cp._running and len(store.history) == n + 1, 5)


def test_copy_cmdline(env, qapp):
    w, cp, store, _ = env
    _new_saved(cp, "c", "getprop {属性:prop=ro.build.type}\nls -l '/data/a b'")
    cp._copy_cmdline(cp._editing())
    text = qapp.clipboard().text()
    assert text.splitlines() == [f"adb -s {SERIAL} shell 'getprop ro.build.type'",
                                 f"adb -s {SERIAL} shell 'ls -l '\"'\"'/data/a b'\"'\"''"]


def test_close_with_unsaved_changes_can_cancel(env, qapp, monkeypatch):
    w, cp, store, _ = env
    cp._new()
    cp.f_name.edit.setText("未保存")
    _choice(monkeypatch, None)
    w.close()
    assert w.isVisible(), "选择取消时不关闭窗口"
    _choice(monkeypatch, "discard")
    w.close()
    assert not w.isVisible()
    w.show()                                               # 让夹具的收尾正常执行
