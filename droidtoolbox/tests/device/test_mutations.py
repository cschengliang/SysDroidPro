import hashlib
from pathlib import Path
import threading
import time
import uuid

import pytest

from app.backend import api
from app.backend._base import quote_args
from app.backend.models import InstallOptions, ScrcpyOptions

pytestmark = [pytest.mark.device, pytest.mark.destructive]
APK = Path(__file__).parents[1] / "fixtures/apk/backend-test.apk"
PACKAGE = "com.droidtoolbox.backendtest"


def test_root_and_noncore_init_restore(rooted, serial):
    backend = rooted
    states = {row.name: row.state for row in backend.list_init_services(serial)}
    before = states.get("traced_probes")
    if before not in ("running", "stopped"):
        pytest.skip("traced_probes 没有可稳定恢复的状态")
    changed = "stopped" if before == "running" else "running"
    try:
        backend.control_init_service(serial, "traced_probes", "stop" if changed == "stopped" else "start")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if backend.get_props(serial).get("init.svc.traced_probes") == changed:
                break
            time.sleep(.05)
        assert backend.get_props(serial).get("init.svc.traced_probes") == changed
    finally:
        backend.control_init_service(serial, "traced_probes", "start" if before == "running" else "stop")
        deadline = time.monotonic() + 3
        while backend.get_props(serial).get("init.svc.traced_probes") != before and time.monotonic() < deadline:
            time.sleep(.05)
        assert backend.get_props(serial).get("init.svc.traced_probes") == before


def test_settings_restore_exact_values(device_backend, serial):
    backend = device_backend
    key = "dtb_test_" + uuid.uuid4().hex
    for ns in ("system", "secure", "global"):
        original = backend.list_settings(serial, ns, 0)
        try:
            backend.put_setting(serial, ns, key, "中文 = a'b", 0)
            assert backend.list_settings(serial, ns, 0)[key] == "中文 = a'b"
            backend.put_setting(serial, ns, key, "", 0)
            assert backend.list_settings(serial, ns, 0)[key] == ""
        finally:
            if key in original:
                backend.put_setting(serial, ns, key, original[key], 0)
            else:
                backend.delete_setting(serial, ns, key, 0)
            assert backend.list_settings(serial, ns, 0).get(key) == original.get(key)


def test_existing_debug_property_restore(device_backend, serial):
    backend = device_backend
    props = backend.get_props(serial)
    key = next((key for key in props if key.startswith("debug.dtb.")), None)
    if key is None:
        pytest.skip("没有预存 debug.dtb.*；属性服务不能删除键，为恢复键不存在的状态不创建新键")
    try:
        backend.set_prop(serial, key, "dtb_test")
        assert backend.get_props(serial)[key] == "dtb_test"
    finally:
        backend.set_prop(serial, key, props[key])


def test_file_roundtrip_rename_read_delete(device_backend, serial, tmp_path):
    backend = device_backend
    base = "/data/local/tmp/dtb_test"
    base_existed = backend.shell(serial, quote_args("test", "-d", base)).exit_code == 0
    remote = base + "/" + uuid.uuid4().hex
    source = tmp_path / "中文 a'b.txt"
    source.write_bytes(("中文 a'b\n" * 50000).encode("utf-8"))
    folder = tmp_path / "目录 中文"
    (folder / "空目录").mkdir(parents=True)
    (folder / "子文件.txt").write_text("目录内容", encoding="utf-8")
    backend.mkdir(serial, remote)
    try:
        backend.push(serial, [str(source), str(folder)], remote)
        assert source.name in {row.name for row in backend.list_dir(serial, remote)}
        renamed = remote + "/new 中文.txt"
        backend.rename(serial, remote + "/" + source.name, renamed)
        text = backend.read_text(serial, renamed, 13)
        assert "已截断" in text and f"{source.stat().st_size} 字节" in text
        paths = backend.pull(serial, [renamed, remote + "/" + folder.name], str(tmp_path / "download"))
        assert hashlib.sha256(Path(paths[0]).read_bytes()).digest() == hashlib.sha256(source.read_bytes()).digest()
        assert (Path(paths[1]) / "子文件.txt").read_text("utf-8") == "目录内容"
        assert (Path(paths[1]) / "空目录").is_dir()
        backend.delete(serial, [renamed, remote + "/" + folder.name])
        assert not backend.list_dir(serial, remote)
    finally:
        backend.delete(serial, [remote])
        if not base_existed:
            assert backend.shell(serial, quote_args("rmdir", "--", base)).exit_code == 0


def test_dedicated_apk_lifecycle_permissions_components(rooted, serial, tmp_path):
    backend = rooted
    if any(row.name == PACKAGE for row in backend.list_packages(serial)):
        pytest.skip("测试包已由用户安装，保留原状")
    assert APK.is_file()
    try:
        backend.install(serial, [str(APK)], InstallOptions(user=0))
        detail = backend.package_detail(serial, PACKAGE)
        assert detail.info.version_code == 1 and not detail.info.system and detail.installed_users == [0]
        backend.clear_data(serial, PACKAGE)
        backend.force_stop(serial, PACKAGE)
        backend.set_enabled(serial, PACKAGE, False)
        assert not backend.package_detail(serial, PACKAGE).info.enabled
        backend.set_enabled(serial, PACKAGE, True)
        components = backend.package_components(serial, PACKAGE)
        component = next(row for row in components if row.kind == "receiver")
        identifier = PACKAGE + "/" + component.name
        backend.set_component_enabled(serial, identifier, False)
        assert not next(row for row in backend.package_components(serial, PACKAGE) if row.name == component.name).enabled
        backend.set_component_enabled(serial, identifier, True)
        backend.set_permission(serial, PACKAGE, "android.permission.CAMERA", True)
        assert next(row for row in backend.package_detail(serial, PACKAGE).permissions if row.name.endswith("CAMERA")).granted
        backend.set_permission(serial, PACKAGE, "android.permission.CAMERA", False)
        exported = backend.extract_apk(serial, PACKAGE, str(tmp_path))
        assert len(exported) == 1 and Path(exported[0]).is_file()
        assert hashlib.sha256(Path(exported[0]).read_bytes()).digest() == hashlib.sha256(APK.read_bytes()).digest()
        with pytest.raises(api.CommandFailed, match="没有可启动"):
            backend.launch(serial, PACKAGE)
        backend.uninstall(serial, PACKAGE, user=0)
        assert PACKAGE not in {row.name for row in backend.list_packages(serial)}
    finally:
        if PACKAGE in {row.name for row in backend.list_packages(serial)}:
            backend.uninstall(serial, PACKAGE)
        assert PACKAGE not in {row.name for row in backend.list_packages(serial)}


def test_scrcpy_real_process_stops_and_keeps_settings(device_backend, serial):
    backend = device_backend
    before = {ns: backend.list_settings(serial, ns) for ns in ("system", "global")}
    output, errors, done = [], [], threading.Event()
    handle = backend.start_scrcpy(serial, ScrcpyOptions(max_size=480, stay_awake=False, audio=False, window_title="DTB integration test"),
        lambda text, stream: output.append(text), lambda error: (errors.append(error), done.set()))
    try:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and not done.is_set() and not any("Texture:" in text for text in output):
            time.sleep(.05)
        assert any("Texture:" in text for text in output), "".join(output)
    finally:
        handle.stop()
    assert done.wait(3) and errors == [None]
    assert backend.list_settings(serial, "system").get("show_touches") == before["system"].get("show_touches")
    assert backend.list_settings(serial, "global").get("stay_on_while_plugged_in") == before["global"].get("stay_on_while_plugged_in")
