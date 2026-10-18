import re
import struct
import threading
import time

import pytest

from app.backend import api, parsers as P

pytestmark = pytest.mark.device


def test_inventory_versions_and_detail(device_backend, serial):
    backend = device_backend
    started = time.monotonic()
    devices = backend.list_devices()
    assert time.monotonic() - started < 1
    assert next(device for device in devices if device.serial == serial).api == 36
    tools = backend.tool_info()
    assert tools.adb_version and tools.scrcpy_version
    detail = backend.device_detail(serial)
    assert detail.api == 36 and detail.build_type == "userdebug" and detail.kernel_full.startswith("Linux version")
    assert detail.mem_total > detail.mem_available > 0
    assert detail.partition_fingerprints and detail.battery is not None
    assert backend.list_apex_modules(serial)


def test_props_settings_users_services_hals_and_process(device_backend, serial):
    backend = device_backend
    assert backend.get_props(serial)["ro.build.fingerprint"] == P.props(backend.shell(serial, "getprop").stdout)["ro.build.fingerprint"]
    assert backend.get_prop_contexts(serial)
    assert all(backend.list_settings(serial, ns) for ns in ("system", "secure", "global"))
    assert backend.list_users(serial)[0].id == 0
    assert backend.list_binder_services(serial) and backend.list_init_services(serial)
    hals = backend.list_hals(serial)
    assert any(hal.kind == "hidl" for hal in hals) and any(hal.kind == "aidl" for hal in hals)
    processes = backend.list_processes(serial)
    assert processes and backend.process_detail(serial, processes[0].pid).pid == processes[0].pid
    with pytest.raises(api.CommandFailed):
        backend.process_detail(serial, 2147483647)


def test_packages_components_and_dump(device_backend, serial):
    backend = device_backend
    packages = backend.list_packages(serial)
    assert packages and packages == sorted(packages, key=lambda row: row.name)
    detail = backend.package_detail(serial, "com.android.settings")
    assert detail.info.system and detail.info.apk_path.endswith(".apk") and detail.installed_users == [0]
    assert backend.package_components(serial, "com.android.settings")
    assert "Package [com.android.settings]" in backend.dump_package(serial, "com.android.settings")


def test_files_screenshot_and_dropbox(device_backend, serial):
    backend = device_backend
    assert backend.list_dir(serial, "/sdcard")
    root = backend.list_dir(serial, "/")
    assert next(row for row in root if row.name == "sdcard").is_dir
    png = backend.screenshot(serial)
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = map(int, re.findall(r"\d+", backend.device_detail(serial).resolution)[:2])
    assert struct.unpack(">II", png[16:24]) == (width, height)
    records = backend.list_crash_files(serial)
    assert records == sorted(records, key=lambda row: row.mtime or 0, reverse=True)
    dropbox = next((row for row in records if row.kind == "dropbox" and row.name.startswith("SYSTEM_BOOT@")), None)
    if dropbox:
        assert backend.read_text(serial, dropbox.path)


def test_logcat_dmesg_and_missing_device_exit(device_backend, serial):
    backend = device_backend
    received, exited = threading.Event(), threading.Event()
    errors = []
    handle = backend.start_logcat(serial, ["main", "system"], lambda batch: received.set(), lambda error: (errors.append(error), exited.set()), tail=10)
    try:
        assert received.wait(5)
    finally:
        handle.stop()
    assert exited.wait(3) and errors == [None]
    exited.clear()
    errors.clear()
    handle = backend.start_command("no-such-serial-0000", ["shell", "echo test"], lambda *args: None,
                                   lambda code, error: (errors.append(error), exited.set()))
    assert exited.wait(3) and isinstance(errors[0], api.DeviceNotFound)
    handle.stop()
    exited.clear()
    errors.clear()
    handle = backend.start_logcat(serial, ["dmesg"], lambda batch: received.set(), lambda error: (errors.append(error), exited.set()))
    try:
        if not backend.root_state(serial).adbd_root:
            assert exited.wait(3) and isinstance(errors[0], api.PermissionDenied)
    finally:
        handle.stop()


def test_shell_state_partial_output_and_interrupt(device_backend, serial):
    backend = device_backend
    output, done = [], threading.Event()
    session = backend.open_shell(serial, lambda text, stream: output.append(text), lambda error: done.set())
    try:
        session.send("cd /data/local/tmp; export DTB_SESSION_VALUE=kept")
        session.send("printf '%s' '中文'; printf '%s\\n' \"$DTB_SESSION_VALUE\"; pwd")
        deadline = time.monotonic() + 3
        while "/data/local/tmp" not in "".join(output) and time.monotonic() < deadline:
            time.sleep(.02)
        assert "中文kept" in "".join(output) and "__DTB_PID__" not in "".join(output)
        session.send("sleep 30; echo DTB_INTERRUPTED")
        time.sleep(.15)
        session.interrupt()
        deadline = time.monotonic() + 3
        while "DTB_INTERRUPTED" not in "".join(output) and time.monotonic() < deadline:
            time.sleep(.02)
        assert "DTB_INTERRUPTED" in "".join(output)
        session.send("echo DTB_SESSION_ALIVE")
        deadline = time.monotonic() + 2
        while "DTB_SESSION_ALIVE" not in "".join(output) and time.monotonic() < deadline:
            time.sleep(.02)
        assert "DTB_SESSION_ALIVE" in "".join(output)
    finally:
        session.stop()
    assert done.wait(3)
