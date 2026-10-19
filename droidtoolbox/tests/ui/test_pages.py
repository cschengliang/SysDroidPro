"""离屏界面测试（演示后端）：每页能打开，关键交互流程的回调不抛异常、状态正确。

模态对话框（确认框、输入框、对比结果）用 monkeypatch 自动应答；命令库与快照写到临时目录。
截图写到 pytest 的临时目录：pytest --basetemp=out 后在 out/ 下找 *.png。
"""
from __future__ import annotations

import sys
import traceback

import pytest
import tbui

from app.backend.mock import MockBackend
from app.backend.models import InstallOptions
from tests.conftest import pump, wait_until

pytestmark = pytest.mark.ui
SERIAL = "3A281FDH2000XY"


@pytest.fixture(scope="module")
def win(qapp, tmp_path_factory):
    import app.ui.snapshot as snapshot
    from app.core.commands import CommandStore
    from app.core.settings import Settings
    from app.core.state import AppState
    from app.main_window import MainWindow
    tmp = tmp_path_factory.mktemp("ui")
    old_dir, snapshot.DATA_DIR = snapshot.DATA_DIR, tmp
    errors: list[str] = []
    old_hook = sys.excepthook
    sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
    s = Settings(tmp / "settings.ini")
    s.set("export_dir", str(tmp / "export"))
    s.set("last_page", "devices")
    st = AppState(MockBackend(), s, CommandStore(tmp / "commands.json"))
    w = MainWindow(st)
    w.resize(1360, 860)
    w.show()
    st.start()
    pump(qapp, 0.8)
    w.errors = errors
    w.shots = tmp
    yield w
    w.close()
    pump(qapp, 0.2)
    sys.excepthook = old_hook
    snapshot.DATA_DIR = old_dir


@pytest.fixture(autouse=True)
def no_callback_errors(win, monkeypatch):
    monkeypatch.setattr(tbui, "confirm", lambda *a, **k: True)   # 所有确认框自动点“确定”
    win.errors.clear()
    yield
    assert not win.errors, "回调中出现未处理异常：\n" + "\n".join(win.errors)


def shot(win, name):
    win.grab().save(str(win.shots / f"{name}.png"))


def test_default_theme_is_light(win):
    from app.core.settings import DEFAULTS
    assert DEFAULTS["theme"] == "light"
    assert tbui.theme().name == "light"


def test_every_page_opens(win, qapp):
    assert list(win.pages) == ["devices", "commands", "shell", "logcat", "bugreport", "props", "settings_db", "services",
                               "apps", "files", "mirror", "launcher", "prefs"]
    for pid in win.pages:
        win.open_page(pid)
        pump(qapp, 0.7)
        assert win.stack.currentWidget() is win.pages[pid]
        shot(win, f"page-{pid}")


def test_auto_root_on_connect(win, qapp):
    b = win.state.backend
    assert wait_until(qapp, lambda: b._st(SERIAL)["root"], 5), "userdebug 设备连接后应自动 root"
    assert not b._st("192.168.1.23:5555")["root"], "user 固件不应 root"


def test_remount_flow_with_reboot(win, qapp):
    """remount → 需要重启 → 确认 → 重启 → 设备回来后自动 root + remount → /system 可写。"""
    dp, b = win.pages["devices"], win.state.backend
    win.state.select(SERIAL)
    win.open_page("devices")
    pump(qapp, 0.6)
    dp._remount_flow(SERIAL)
    assert wait_until(qapp, lambda: dp._pending_remount == SERIAL, 5), "需要重启时应等待设备重新连接"
    assert wait_until(qapp, lambda: b._st(SERIAL)["rw"], 15), "设备回来后应自动完成 remount"
    assert wait_until(qapp, lambda: dp.root is not None and dp.root.system_writable, 5)
    text = dp._log.out.toPlainText()
    assert "adb remount" in text and "adb reboot" in text and "remount succeeded" in text
    shot(win, "devices-remounted")


def test_version_copy_and_apex(win, qapp):
    dp = win.pages["devices"]
    assert wait_until(qapp, lambda: dp.detail is not None, 5)
    dp._copy_versions()
    clip = qapp.clipboard().text()
    assert "构建指纹" in clip and "与 system 不一致" in clip    # 演示数据里 vendor 指纹与 system 不同
    dp.apex_toggle.click()
    assert wait_until(qapp, lambda: dp.apex_table.table_model.rowCount() > 0)
    assert dp.apex_table.isVisibleTo(dp)


def test_adb_commands_run_persistent_and_save(win, qapp, monkeypatch):
    cp = win.pages["commands"]
    win.open_page("commands")
    pump(qapp, 0.3)
    store = win.state.commands
    cp._select("builtin:当前焦点窗口")
    cp._run()
    assert wait_until(qapp, lambda: not cp._running and "mCurrentFocus" in cp.result.plain(), 5)
    assert "exit 0" in cp.status.text()
    shot(win, "commands-result")
    # 参数占位
    monkeypatch.setattr(cp, "_ask_values", lambda c: {"属性名": "ro.build.type"})
    cp._select("builtin:属性的 SELinux 上下文")
    cp._run()
    assert wait_until(qapp, lambda: not cp._running and "getprop -Z ro.build.type" in cp.result.plain(), 5)
    # 持续运行 + 停止
    cp._select("builtin:实时 top")
    cp._run()
    assert wait_until(qapp, lambda: "system_server" in cp.result.plain(), 5)
    assert cp.stop_btn.isVisible()
    cp._stop()
    assert wait_until(qapp, lambda: not cp._running, 5)
    assert "已停止" in cp.result.plain()
    # 新建并保存，写入 commands.json
    cp._new()
    cp.f_name.edit.setText("查看 adbd 状态")
    cp.f_script.setPlainText("getprop init.svc.adbd")
    cp._save()
    assert any(c.name == "查看 adbd 状态" for c in store.user)
    assert "查看 adbd 状态" in store.path.read_text("utf-8")
    assert len(store.history) >= 3


def test_settings_db_edit_and_snapshot(win, qapp, monkeypatch):
    import app.ui.snapshot as snapshot
    sp = win.pages["settings_db"]
    win.open_page("settings_db")
    assert wait_until(qapp, lambda: sp.table.table_model.rowCount() > 0)
    monkeypatch.setattr(snapshot, "ask_text", lambda *a, **k: "测试快照")
    captured = {}
    monkeypatch.setattr(snapshot, "show_diff", lambda parent, title, rows, noun="键": captured.setdefault("rows", rows))
    sp.snap._record()
    assert wait_until(qapp, lambda: bool(sp.snap.pick.currentData()), 5)
    b = win.state.backend
    b.put_setting(SERIAL, "global", "window_animation_scale", "0.5")
    b.put_setting(SERIAL, "secure", "dtb_new_key", "1", 0)
    sp.snap._compare()
    assert wait_until(qapp, lambda: "rows" in captured, 5)
    keys = {(r[0], r[1]) for r in captured["rows"]}
    assert ("changed", "global/window_animation_scale") in keys and ("added", "secure/dtb_new_key") in keys
    sp.ns.set_value("system")
    assert wait_until(qapp, lambda: "show_touches" in sp._data, 5)
    shot(win, "settings-db")
    # 常用标签：读取当前值、写入预设
    from app.pages.settings_db import PRESETS
    sp.ns.set_value("presets")
    assert sp.stack.currentIndex() == 1 and sp.filter.isVisible() and not sp.add.isVisible()

    def value_of(key):
        return next((v for p, v in sp.ptable.table_model.rows if p.key == key), "missing")
    assert wait_until(qapp, lambda: value_of("window_animation_scale") == "0.5", 5)   # 上面改过
    assert len(sp.ptable.table_model.rows) == len(PRESETS)
    touch = next(p for p in PRESETS if p.key == "show_touches")
    sp._put_preset(touch, "1")
    assert wait_until(qapp, lambda: value_of("show_touches") == "1", 5)
    sp.filter.edit.setText("animation")
    pump(qapp, 0.2)
    assert sp.ptable.filter.rowCount() == 2
    sp.filter.edit.setText("")
    shot(win, "settings-presets")
    sp.ns.set_value("global")


def test_props_watch_and_context(win, qapp):
    pp = win.pages["props"]
    win.open_page("props")
    assert wait_until(qapp, lambda: pp.table.table_model.rowCount() > 0)
    pp.ctx_btn.setChecked(True)
    assert wait_until(qapp, lambda: bool(pp._contexts), 5)
    pp.filter.edit.setText("init.svc.vendor.power-hal")
    pump(qapp, 0.2)
    pp.table.selectRow(0)
    pp.watch_btn.setChecked(True)
    assert pp._watched == {"init.svc.vendor.power-hal"}
    win.state.backend.set_prop(SERIAL, "init.svc.vendor.power-hal", "restarting")
    assert wait_until(qapp, lambda: "init.svc.vendor.power-hal" in pp._changed_at, 4)
    shot(win, "props-watch")
    pp.watch_btn.setChecked(False)
    pp.filter.edit.setText("")


def test_services_tabs(win, qapp):
    sv = win.pages["services"]
    win.open_page("services")
    assert wait_until(qapp, lambda: sv.b_table.table_model.rowCount() > 0)
    sv.b_table.selectRow(0)
    assert wait_until(qapp, lambda: "ACTIVITY" in sv.b_view.plain(), 5)
    sv.tabs.seg.set_value("init")
    assert wait_until(qapp, lambda: sv.i_table.table_model.rowCount() > 0)
    sv.i_filter.edit.setText("thermal-hal")
    pump(qapp, 0.2)
    sv.i_table.selectRow(0)
    sv._control("restart")
    assert wait_until(qapp, lambda: win.state.backend._props["init.svc.thermal-hal"] == "restarting", 5)
    sv.tabs.seg.set_value("hal")
    assert wait_until(qapp, lambda: sv.h_table.table_model.rowCount() > 0)
    sv.h_table.selectRow(0)
    sv._hal_debug()
    assert wait_until(qapp, lambda: "演示数据" in sv.h_view.plain(), 5)
    sv.tabs.seg.set_value("proc")
    assert wait_until(qapp, lambda: sv.p_table.table_model.rowCount() > 0)
    sv.p_filter.edit.setText("system_server")
    pump(qapp, 0.2)
    sv.p_table.selectRow(0)
    assert wait_until(qapp, lambda: "/proc/1532/status" in sv.p_view.plain(), 5)
    sv._backtrace()
    assert wait_until(qapp, lambda: "binder:1532_1" in sv.p_view.plain(), 5)
    shot(win, "services-proc")


def test_apps_users_components_dump(win, qapp):
    ap = win.pages["apps"]
    win.open_page("apps")
    assert wait_until(qapp, lambda: ap.user.count() == 3, 5), "应识别出 3 个用户"
    ap.user.setCurrentIndex(ap.user.findData("10"))
    assert wait_until(qapp, lambda: ap.table.table_model.rowCount() == 9, 5)
    ap.kind.set_value("all")
    ap.filter.edit.setText("com.example.shop")
    pump(qapp, 0.3)
    ap.table.selectRow(0)
    assert wait_until(qapp, lambda: ap._detail is not None, 5)
    assert ap._detail.installed_users == [0, 10]
    ap.tabs.seg.set_value("components")
    assert wait_until(qapp, lambda: ap.c_table.table_model.rowCount() > 0, 5)
    ap.c_table.selectRow(0)
    ap._set_components(False)
    assert wait_until(qapp, lambda: any(not c.enabled for c in ap.c_table.table_model.rows), 5)
    shot(win, "apps-components")
    ap.tabs.seg.set_value("dump")
    assert wait_until(qapp, lambda: "Package [com.example.shop]" in ap.dump.plain(), 5)
    ap.strip.run("安装 demo.apk", win.state.backend.install, SERIAL, ["C:/x/demo.apk"], InstallOptions(user=10), done=lambda _: ap.refresh())
    assert wait_until(qapp, lambda: not ap.strip.busy, 6)
    ap.filter.edit.setText("")
    ap.user.setCurrentIndex(0)


def test_logcat_filters(win, qapp):
    lp = win.pages["logcat"]
    win.open_page("logcat")
    assert wait_until(qapp, lambda: lp.view.log_model.rowCount() > 0)
    lp.pause.setChecked(True)
    pump(qapp, 0.2)
    n0 = lp.view.log_model.rowCount()
    pump(qapp, 1.0)
    assert lp.view.log_model.rowCount() == n0 and lp._held
    lp.pause.setChecked(False)
    pump(qapp, 0.3)
    assert not lp._held
    assert not hasattr(lp, "pid") and not hasattr(lp, "avc")
    # 级别下拉 + 正则按钮（与过滤框内的正则开关同步）
    lp.level.setCurrentIndex(lp.level.findData("W"))
    lp.regex.setChecked(True)
    assert lp.filter.options()["regex"]
    lp.filter.edit.setText("^Ok|Package")
    pump(qapp, 0.3)
    rows = [lp.view.proxy.index(i, 0).data(256) for i in range(lp.view.proxy.rowCount())]
    assert rows and all(r.level in "WEFA" and (r.tag.startswith("Ok") or "Package" in r.tag + r.message) for r in rows)
    lp.filter.edit.setText("([")
    pump(qapp, 0.1)
    assert lp.filter.property("invalid") is True
    lp.regex.setChecked(False)
    assert not lp.filter.options()["regex"] and lp.filter.property("invalid") is False
    lp.filter.edit.setText("")
    lp.level.setCurrentIndex(0)
    assert lp.filter.width() > 400, "过滤框应占工具栏剩余宽度"
    lp.buffers.setCurrentIndex(lp.buffers.findData("dmesg"))
    assert wait_until(qapp, lambda: any("init: " in r.message or "healthd" in r.message for r in lp.view.log_model.rows[-80:]), 5)
    lp.buffers.setCurrentIndex(0)


def test_shell_menu_from_command_library(win, qapp):
    sp = win.pages["shell"]
    win.open_page("shell")
    assert wait_until(qapp, lambda: sp._session is not None)
    sp._fill_menu()
    titles = [a.text() for a in sp.cmd_menu.actions()]
    assert "系统信息" in titles and "管理命令库…" in titles
    sp._run_cmd("getenforce")
    assert wait_until(qapp, lambda: "Enforcing" in sp.term.out.toPlainText())


def test_files_and_bugreport(win, qapp):
    fp = win.pages["files"]
    win.open_page("files")
    assert wait_until(qapp, lambda: fp.table.table_model.rowCount() > 0)
    fp.go("/sdcard/Download")
    assert wait_until(qapp, lambda: fp.cwd == "/sdcard/Download")
    fp._back()
    assert wait_until(qapp, lambda: fp.cwd == "/sdcard")
    bp = win.pages["bugreport"]
    win.open_page("bugreport")
    assert wait_until(qapp, lambda: bp.table.table_model.rowCount() > 0)
    bp.table.selectRow(0)
    assert wait_until(qapp, lambda: len(bp.viewer.plain()) > 20)


def test_mirror_quick_start(win, qapp):
    mp = win.pages["mirror"]
    mp.quick_start()                       # 未配置 scrcpy：提示错误，不崩溃
    pump(qapp, 0.3)
    assert mp._proc is None
    win.state.backend.configure(None, None, "C:/tools/scrcpy.exe")
    mp.quick_start()
    assert mp._proc is not None
    win.open_page("mirror")
    assert wait_until(qapp, lambda: "窗口已打开" in mp.log.out.toPlainText(), 3)
    shot(win, "mirror-running")
    mp._toggle()
    assert mp._proc is None


def test_theme_toggle(win, qapp):
    win.open_page("devices")
    tbui.theme().toggle()
    pump(qapp, 0.4)
    shot(win, "devices-dark")
    tbui.theme().toggle()
    pump(qapp, 0.2)
    assert tbui.theme().name == "light"
