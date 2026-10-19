from concurrent.futures import ThreadPoolExecutor
import subprocess
import threading

import pytest

from app.backend import parsers as P
from app.backend.adb import AdbBackend
from tests.fakes import FakeRunner, fake_backend, fixture

PACKAGE = "com.droidtoolbox.backendtest"
CAMERA = "android.permission.CAMERA"
RECORD_AUDIO = "android.permission.RECORD_AUDIO"
INTERNET = "android.permission.INTERNET"
SIGNATURE = "android.permission.WRITE_SECURE_SETTINGS"
UNKNOWN = "no.such.permission"
QUERY = "pm list permissions -f -g"


@pytest.fixture
def make_backend():
    backends = []

    def make(runner=None):
        backend = fake_backend(AdbBackend, runner)
        backends.append(backend)
        return backend

    yield make
    for backend in backends:
        backend._pool.shutdown(wait=True)


def permission(detail, name=CAMERA):
    return next(row for row in detail.permissions if row.name == name)


def query_calls(runner):
    return [args for args in runner.calls if args[-1] == QUERY]


def test_permission_definitions_real_emulator_output():
    definitions = P.permission_definitions(fixture("permission_definitions_pm.emu36"))
    assert P.permission_definitions(fixture("permission_definitions_pm_shell.emu36")) == definitions
    assert definitions[CAMERA] and definitions[RECORD_AUDIO]
    assert not definitions[INTERNET] and not definitions[SIGNATURE]
    assert UNKNOWN not in definitions
    # The tempting filtered command really omits dangerous permissions on API 36.
    filtered = fixture("permission_definitions_pm_d_u.emu36")
    assert CAMERA not in filtered and RECORD_AUDIO not in filtered
    assert "Dangerous Permissions:" in filtered


def test_definition_parser_requires_permission_record_and_exact_protection_flag():
    output = """All Permissions:
+ group:android.permission-group.CAMERA
  protectionLevel:dangerous
  + permission:missing.level
    label:dangerous
+ group:another.group
  protectionLevel:dangerous
  + permission:normal.permission
    description:dangerous|instant
    protectionLevel:normal
  + permission:signature.permission
    protectionLevel:signature|privileged
  + permission:almost.dangerous
    protectionLevel:notdangerous
  + permission:real.dangerous
    protectionLevel:dangerous|instant
  + permission:truncated.permission
"""
    assert P.permission_definitions(output) == {
        "normal.permission": False, "signature.permission": False,
        "almost.dangerous": False, "real.dangerous": True,
    }
    assert P.permission_definitions("Error: permission service unavailable\n") == {}


def test_real_ungranted_camera_is_classified_without_inventing_grant():
    output = fixture(f"dumpsys_package_{PACKAGE}.emu36")
    definitions = P.permission_definitions(fixture("permission_definitions_pm.emu36"))
    assert P.pending_permissions(output, PACKAGE) == [CAMERA]
    assert not permission(P.package_detail(output, PACKAGE)).runtime
    classified = P.package_detail(output, PACKAGE, permission_definitions=definitions)
    assert permission(classified).runtime and not permission(classified).granted
    assert permission(classified, "android.permission.POST_NOTIFICATIONS").runtime


def test_definitions_preserve_install_and_selected_user_runtime_states():
    package = "com.example.test"
    output = fixture("package_multiuser.synthetic").replace(
        f"{INTERNET}: granted=true", f"{INTERNET}: granted=false"
    ).replace(
        f"{CAMERA}: granted=true", f"{CAMERA}: granted=true\n        {RECORD_AUDIO}: granted=true"
    )
    assert P.pending_permissions(output, package, 0) == []
    assert P.pending_permissions(output, package, 10) == [RECORD_AUDIO]
    zero = P.package_detail(output, package, permission_definitions={CAMERA: False, RECORD_AUDIO: False, INTERNET: True})
    ten = P.package_detail(output, package, 10, permission_definitions={CAMERA: False, RECORD_AUDIO: True, INTERNET: True})
    assert permission(zero).runtime and permission(zero).granted
    assert permission(zero, RECORD_AUDIO).runtime and permission(zero, RECORD_AUDIO).granted
    assert not permission(ten, INTERNET).runtime and not permission(ten, INTERNET).granted
    assert permission(ten).runtime and not permission(ten).granted
    assert permission(ten, RECORD_AUDIO).runtime and not permission(ten, RECORD_AUDIO).granted


def test_all_pending_permissions_use_one_query_and_share_cache_across_packages(make_backend):
    runner = FakeRunner.from_fixtures()
    output = fixture(f"dumpsys_package_{PACKAGE}.emu36").replace(
        "      " + CAMERA, "\n".join("      " + name for name in (CAMERA, RECORD_AUDIO, INTERNET, SIGNATURE, UNKNOWN))
    )
    runner.add(("shell", f"dumpsys package {PACKAGE}"), (0, output, ""))
    runner.add(("shell", "dumpsys package com.example.test"), (0, fixture("package_multiuser.synthetic"), ""))
    backend = make_backend(runner)
    detail = backend.package_detail("s", PACKAGE)
    assert {row.name: (row.runtime, row.granted) for row in detail.permissions} == {
        CAMERA: (True, False), RECORD_AUDIO: (True, False), INTERNET: (False, False),
        SIGNATURE: (False, False), UNKNOWN: (False, False),
        "android.permission.POST_NOTIFICATIONS": (True, False),
    }
    backend.package_detail("s", PACKAGE)
    other = backend.package_detail("s", "com.example.test", 10)
    assert permission(other, RECORD_AUDIO).runtime and not permission(other, RECORD_AUDIO).granted
    assert query_calls(runner) == [["adb", "-s", "s", "shell", QUERY]]


def test_cache_is_per_device(make_backend):
    runner = FakeRunner.from_fixtures().add(
        lambda args: args[2] == "second" and args[-1] == QUERY,
        (0, f"  + permission:{CAMERA}\n    protectionLevel:normal\n", ""),
    )
    backend = make_backend(runner)
    for _ in range(2):
        assert permission(backend.package_detail("first", PACKAGE)).runtime
        assert not permission(backend.package_detail("second", PACKAGE)).runtime
    assert [args[2] for args in query_calls(runner)] == ["first", "second"]


@pytest.mark.parametrize("result", [
    (1, "", "permission service failed"),
    (1, "  + permission:android.permission.CAMERA\n    protectionLevel:dangerous\n", "failure"),
    (1, "", "Permission denied"),
    (1, "", "error: device offline"),
    subprocess.TimeoutExpired("adb", 10),
    FileNotFoundError("adb disappeared"),
    (0, "", ""),
    (0, "Error: unsupported command\n", ""),
])
def test_query_failure_falls_back_and_retries_without_failing_detail(make_backend, result):
    runner = FakeRunner.from_fixtures().add(("shell", QUERY), result)
    backend = make_backend(runner)
    detail = backend.package_detail("s", PACKAGE)
    assert not permission(detail).runtime and not permission(detail).granted
    assert permission(detail, "android.permission.POST_NOTIFICATIONS").runtime
    runner.add(("shell", QUERY), (0, fixture("permission_definitions_pm.emu36"), ""))
    assert permission(backend.package_detail("s", PACKAGE)).runtime
    assert len(query_calls(runner)) == 2


def test_no_definition_query_when_dump_already_classifies_every_permission(make_backend):
    runner = FakeRunner.from_fixtures().add(
        ("shell", f"dumpsys package {PACKAGE}"),
        (0, fixture(f"dumpsys_package_{PACKAGE}.emu36").replace("      " + CAMERA + "\n", ""), ""),
    )
    make_backend(runner).package_detail("s", PACKAGE)
    assert not query_calls(runner)


@pytest.mark.parametrize("offline", ["", "s offline\n", "s unauthorized\n"])
def test_device_reappearing_invalidates_only_its_cache(make_backend, offline):
    runner = FakeRunner.from_fixtures().add(("devices", "-l"), (0, "s device\nother device\n", ""))
    backend = make_backend(runner)
    backend.list_devices()
    backend.package_detail("s", PACKAGE)
    backend.package_detail("other", PACKAGE)
    runner.add(("devices", "-l"), (0, offline + "other device\n", ""))
    backend.list_devices()
    runner.add(("devices", "-l"), (0, "s device\nother device\n", ""))
    backend.list_devices()
    runner.add(("shell", QUERY), (0, f"+ permission:{CAMERA}\n  protectionLevel:normal\n", ""))
    assert not permission(backend.package_detail("s", PACKAGE)).runtime
    assert permission(backend.package_detail("other", PACKAGE)).runtime
    assert [args[2] for args in query_calls(runner)] == ["s", "other", "s"]


def test_concurrent_details_query_once_without_blocking_other_devices(make_backend):
    runner = FakeRunner.from_fixtures()
    started, release = threading.Event(), threading.Event()

    def slow_query(args):
        started.set()
        assert release.wait(3)
        return 0, fixture("permission_definitions_pm.emu36"), ""

    runner.add(lambda args: args[2] == "slow" and args[-1] == QUERY, slow_query)
    backend = make_backend(runner)
    with ThreadPoolExecutor(max_workers=3) as pool:
        first = pool.submit(backend.package_detail, "slow", PACKAGE)
        try:
            assert started.wait(2)
            second = pool.submit(backend.package_detail, "slow", PACKAGE)
            fast = pool.submit(backend.package_detail, "fast", PACKAGE)
            assert permission(fast.result(timeout=2)).runtime
        finally:
            release.set()
        assert permission(first.result(timeout=2)).runtime
        assert permission(second.result(timeout=2)).runtime
    assert sorted(args[2] for args in query_calls(runner)) == ["fast", "slow"]


def test_new_transport_with_same_online_serial_invalidates_definitions(make_backend):
    runner = FakeRunner.from_fixtures().add(("devices", "-l"), (0, "s device transport_id:1\n", ""))
    backend = make_backend(runner)
    backend.list_devices()
    assert permission(backend.package_detail("s", PACKAGE)).runtime
    runner.add(("devices", "-l"), (0, "s device transport_id:2\n", ""))
    backend.list_devices()
    runner.add(("shell", QUERY), (0, f"+ permission:{CAMERA}\n  protectionLevel:normal\n", ""))
    assert not permission(backend.package_detail("s", PACKAGE)).runtime
    assert len(query_calls(runner)) == 2


def test_disconnect_during_query_does_not_repopulate_old_session_cache(make_backend):
    started, release = threading.Event(), threading.Event()
    runner = FakeRunner.from_fixtures().add(("disconnect", "s"), (0, "disconnected s\n", ""))

    def slow_query(args):
        started.set()
        assert release.wait(3)
        return 0, fixture("permission_definitions_pm.emu36"), ""

    runner.add(("shell", QUERY), slow_query)
    backend = make_backend(runner)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(backend.package_detail, "s", PACKAGE)
        try:
            assert started.wait(2)
            backend.disconnect("s")
        finally:
            release.set()
        assert not permission(pending.result(timeout=2)).runtime
    runner.add(("shell", QUERY), (0, fixture("permission_definitions_pm.emu36"), ""))
    assert permission(backend.package_detail("s", PACKAGE)).runtime
    assert len(query_calls(runner)) == 2


@pytest.mark.parametrize("operation", ["configure", "restart_server", "connect", "root"])
def test_explicit_session_changes_invalidate_definitions(make_backend, operation):
    runner = FakeRunner.from_fixtures()
    runner.add(("connect", "host:5555"), (0, "connected to host:5555\n", ""))
    runner.add(("kill-server",), (0, "", ""))
    runner.add(("start-server",), (0, "", ""))
    backend = make_backend(runner)
    backend.package_detail("host:5555", PACKAGE)
    if operation == "configure":
        backend.configure(None, None, None)
    elif operation == "connect":
        backend.connect("host:5555")
    elif operation == "root":
        backend.root("host:5555")
    else:
        backend.restart_server()
    backend.package_detail("host:5555", PACKAGE)
    assert len(query_calls(runner)) == 2
