"""后端契约测试：任何 Backend 实现都必须通过。

现在只对 MockBackend 运行；实现 AdbBackend 后，在 BACKENDS 里加入用假 runner 构造的实例
（以及 DTB_DEVICE 存在时的真实实例），同一组断言即可复用。
"""
from __future__ import annotations

import inspect
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from app.backend import api, models
from app.backend.mock import MockBackend
from app.backend.adb import AdbBackend
from tests.fakes import fake_backend

ROOT = Path(__file__).resolve().parents[1]


def _backends():
    yield pytest.param(MockBackend, id="mock")
    yield pytest.param(lambda: fake_backend(AdbBackend), id="adb-fake")
    if os.environ.get("DTB_DEVICE"):
        yield pytest.param(AdbBackend, id="adb-device", marks=pytest.mark.device)


@pytest.fixture(params=list(_backends()))
def backend(request) -> api.Backend:
    instance = request.param()
    if isinstance(instance, AdbBackend) and not instance._runner.__class__.__module__.startswith("tests."):
        if request.node.originalname == "test_root_and_remount_flow":
            pytest.skip("挂载与 verity 状态机只用假 runner 验证；真机 root 在可恢复用例中单独验证")
        if request.node.originalname in ("test_settings_roundtrip", "test_long_task_progress_and_cancel") and os.environ.get("DTB_DESTRUCTIVE") != "1":
            pytest.skip("此真实契约用例需 DTB_DESTRUCTIVE=1；默认只读")
    return instance


@pytest.fixture
def online(backend) -> models.Device:
    serial = os.environ.get("DTB_DEVICE") if isinstance(backend, AdbBackend) and type(backend._runner).__module__ != "tests.fakes" else None
    d = next((d for d in backend.list_devices() if d.state == "device" and (serial is None or d.serial == serial)), None)
    if d is None:
        pytest.skip("没有处于 device 状态的设备")
    return d


def test_no_abstract_methods_left(backend):
    assert not getattr(type(backend), "__abstractmethods__", None)


def test_backend_package_does_not_import_qt():
    code = ("import sys; import app.backend.api, app.backend.models, app.backend.mock\n"
            "try:\n import app.backend.adb\nexcept ModuleNotFoundError: pass\n"
            "bad=[m for m in sys.modules if m.split('.')[0] in ('PySide6','PyQt6','shiboken6','tbui')]\n"
            "assert not bad, bad")
    r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); " + code],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0, r.stderr


def test_signatures_match_contract(backend):
    """实现的方法签名（参数名与顺序）必须与 api.Backend 一致，界面按关键字调用。"""
    for name, fn in inspect.getmembers(api.Backend, inspect.isfunction):
        if name.startswith("_"):
            continue
        impl = getattr(type(backend), name)
        want = list(inspect.signature(fn).parameters)
        got = list(inspect.signature(impl).parameters)
        assert got[:len(want)] == want, f"{name}: {got} != {want}"


def test_tool_info(backend):
    info = backend.tool_info()
    assert isinstance(info, models.ToolInfo)
    assert isinstance(info.server_running, bool)


def test_list_devices_fast_and_typed(backend):
    t = time.time()
    devs = backend.list_devices()
    assert time.time() - t < 1.0
    assert all(isinstance(d, models.Device) for d in devs)
    assert len({d.serial for d in devs}) == len(devs), "serial 必须唯一"
    for d in devs:
        assert d.transport in ("usb", "wifi", "emulator")
        assert d.battery is None or 0 <= d.battery <= 100


def test_device_detail(backend, online):
    dd = backend.device_detail(online.serial)
    assert isinstance(dd, models.DeviceDetail)
    assert dd.serial == online.serial
    assert dd.battery is None or isinstance(dd.battery, models.BatteryInfo)
    assert all(isinstance(s, models.StorageInfo) and s.used <= s.total for s in dd.storage)


def test_unknown_serial_raises_device_not_found(backend):
    with pytest.raises(api.DeviceNotFound):
        backend.device_detail("no-such-serial-0000")


def test_shell_returns_exit_code_not_exception(backend, online):
    r = backend.shell(online.serial, "this_command_does_not_exist_xyz")
    assert isinstance(r, models.ShellResult)
    assert r.exit_code != 0


def test_props_and_contexts(backend, online):
    props = backend.get_props(online.serial)
    assert isinstance(props, dict) and props
    assert all(isinstance(k, str) and isinstance(v, str) for k, v in props.items())
    ctx = backend.get_prop_contexts(online.serial)
    assert ctx and all(v.startswith("u:object_r:") for v in ctx.values())


def test_set_ro_prop_is_permission_denied(backend, online):
    with pytest.raises(api.PermissionDenied):
        backend.set_prop(online.serial, "ro.build.type", "eng")


def test_settings_roundtrip(backend, online):
    g = backend.list_settings(online.serial, "global")
    assert g and all(isinstance(k, str) and isinstance(v, str) for k, v in g.items())
    before = backend.list_settings(online.serial, "system", 0)
    try:
        backend.put_setting(online.serial, "system", "dtb_test_key", "42", 0)
        assert backend.list_settings(online.serial, "system", 0).get("dtb_test_key") == "42"
        backend.delete_setting(online.serial, "system", "dtb_test_key", 0)
        assert "dtb_test_key" not in backend.list_settings(online.serial, "system", 0)
    finally:
        if "dtb_test_key" in before:
            backend.put_setting(online.serial, "system", "dtb_test_key", before["dtb_test_key"], 0)
        else:
            backend.delete_setting(online.serial, "system", "dtb_test_key", 0)


def test_root_and_remount_flow(backend, online):
    """userdebug：root → remount（可能要求重启）→ 状态反映在 root_state。"""
    rs = backend.root_state(online.serial)
    assert isinstance(rs, models.RootState)
    if rs.build_type == "user":
        pytest.skip("user 固件")
    if not rs.adbd_root:
        backend.root(online.serial, True)
    assert backend.root_state(online.serial).adbd_root
    r = backend.remount(online.serial)
    assert isinstance(r, models.RemountResult) and isinstance(r.output, str)


def test_services_hals_processes(backend, online):
    svcs = backend.list_binder_services(online.serial)
    names = [s.name for s in svcs]
    assert names == sorted(names, key=str.lower)
    assert isinstance(backend.dumpsys(online.serial, names[0]), str)
    inits = backend.list_init_services(online.serial)
    assert inits and all(isinstance(s, models.InitService) for s in inits)
    hals = backend.list_hals(online.serial)
    assert all(h.kind in ("hidl", "aidl") for h in hals)
    procs = backend.list_processes(online.serial)
    assert procs and all(isinstance(p, models.ProcessInfo) for p in procs)
    d = backend.process_detail(online.serial, procs[0].pid)
    assert isinstance(d, models.ProcessDetail) and d.pid == procs[0].pid


def test_users_and_components(backend, online):
    users = backend.list_users(online.serial)
    assert users and users[0].id == 0 and all(isinstance(u, models.AndroidUser) for u in users)
    pkgs = backend.list_packages(online.serial, users[-1].id)
    assert pkgs
    comps = backend.package_components(online.serial, pkgs[0].name, users[-1].id)
    assert all(isinstance(c, models.Component) and c.kind in ("activity", "service", "receiver", "provider") for c in comps)
    assert isinstance(backend.dump_package(online.serial, pkgs[0].name), str)


def test_run_adb_returns_exit_code(backend, online):
    r = backend.run_adb(online.serial, ["get-state"])
    assert isinstance(r, models.ShellResult) and r.exit_code == 0 and "device" in r.stdout
    assert backend.run_adb(online.serial, ["no-such-adb-subcommand"]).exit_code != 0


def test_start_command_stops(backend, online):
    out, done = [], threading.Event()
    codes = []
    h = backend.start_command(online.serial, ["shell", "top -d 1"], lambda t, s: out.append(t),
                              lambda code, err: (codes.append(code), done.set()))
    time.sleep(1.5)
    h.stop()
    assert done.wait(3), "停止后必须调用 on_exit"
    assert codes == [None], "被 stop() 终止时退出码为 None"


def test_apex_modules(backend, online):
    mods = backend.list_apex_modules(online.serial)
    assert all(isinstance(m, models.ApexModule) for m in mods)
    assert [m.name for m in mods] == sorted(m.name for m in mods)


def test_list_dir_sorted_dirs_first(backend, online):
    entries = backend.list_dir(online.serial, "/sdcard")
    assert all(isinstance(e, models.FileEntry) for e in entries)
    assert not any(e.name in (".", "..") for e in entries)
    kinds = [not e.is_dir for e in entries]
    assert kinds == sorted(kinds), "目录必须排在文件前面"


def test_packages(backend, online):
    pkgs = backend.list_packages(online.serial)
    assert pkgs and all(isinstance(p, models.PackageInfo) for p in pkgs)
    d = backend.package_detail(online.serial, pkgs[0].name)
    assert isinstance(d, models.PackageDetail) and d.info.name == pkgs[0].name
    assert all(isinstance(p, models.Permission) for p in d.permissions)


def test_screenshot_is_png(backend, online):
    data = backend.screenshot(online.serial)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"


def _collect_stream(start, seconds=1.0):
    got, exited = [], threading.Event()
    exit_err = []

    def on_exit(err):
        exit_err.append(err)
        exited.set()
    h = start(got.append, on_exit)
    time.sleep(seconds)
    h.stop()
    stopped_at = len(got)
    time.sleep(0.5)
    return h, got, stopped_at, exited, exit_err


def test_logcat_stream_stops_cleanly(backend, online):
    h, got, stopped_at, exited, _ = _collect_stream(
        lambda cb, ex: backend.start_logcat(online.serial, ["main"], cb, ex, tail=50))
    assert got and all(isinstance(batch, list) and all(isinstance(l, str) for l in batch) for batch in got)
    assert len(got) == stopped_at, "stop() 之后不应再回调"
    assert not h.running
    assert exited.wait(2), "流结束时必须调用 on_exit"


def test_long_task_progress_and_cancel(backend, online, tmp_path):
    seen = []
    cancel = threading.Event()

    def progress(p):
        seen.append(p)
        if len(seen) == 3:
            cancel.set()
    real = isinstance(backend, AdbBackend) and type(backend._runner).__module__ != "tests.fakes"
    before = backend.root_state(online.serial) if real else None
    try:
        if before is not None and not before.adbd_root:
            backend.root(online.serial)
        with pytest.raises(api.Cancelled):
            backend.bugreport(online.serial, str(tmp_path), progress=progress, cancel=cancel)
        if real:
            assert not backend._dumpstate_pids(online.serial), "本次启动的 dumpstate 也应随取消退出"
    finally:
        if before is not None and backend.root_state(online.serial).adbd_root != before.adbd_root:
            backend.root(online.serial, before.adbd_root)
    assert all(isinstance(p, models.Progress) for p in seen)
    assert all(p.percent is None or 0 <= p.percent <= 100 for p in seen)


def test_shell_session(backend, online):
    out, exited = [], threading.Event()
    s = backend.open_shell(online.serial, lambda t, stream: out.append((t, stream)), lambda e: exited.set())
    s.send("echo hello_toolbox")
    deadline = time.time() + 3
    while time.time() < deadline and not any("hello_toolbox" in t for t, _ in out):
        time.sleep(0.05)
    assert any("hello_toolbox" in t and st == "stdout" for t, st in out)
    s.stop()
    assert exited.wait(2) and not s.running
