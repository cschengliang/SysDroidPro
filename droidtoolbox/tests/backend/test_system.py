import shlex

import pytest

from app.backend._base import BackendBase
from app.backend._system import SystemMethods
from app.backend import api
from tests.fakes import FakeRunner, fake_backend, fixture


class System(SystemMethods, BackendBase):
    pass


def test_props_context_settings():
    backend = fake_backend(System)
    assert backend.get_props("s")["ro.build.version.sdk"] == "36"
    assert backend.get_prop_contexts("s")
    backend.set_prop("s", "debug.dtb.test", "")
    assert backend.get_props("s")["debug.dtb.test"] == ""
    with pytest.raises(api.PermissionDenied):
        backend.set_prop("s", "ro.build.type", "eng")
    backend.put_setting("s", "secure", "key' 空", "=空'", 10)
    assert backend.list_settings("s", "secure", 10)["key' 空"] == "=空'"
    backend.delete_setting("s", "secure", "key' 空", 10)
    assert "key' 空" not in backend.list_settings("s", "secure", 10)
    backend.put_setting("s", "global", "key", "", 10)
    assert shlex.split(backend._runner.calls[-1][-1]) == ["settings", "put", "global", "key", ""]


def test_hals_keep_partial_output_and_process_names():
    runner = FakeRunner.from_fixtures()
    runner.add(("shell", "lshal --neat -itp"), (200, fixture("lshal.emu36"), fixture("lshal.stderr.emu36")))
    runner.add(lambda args: args[-1].startswith("ps -o PID,NAME"), (0, "PID NAME\n536 codec_service\n", ""))
    backend = fake_backend(System, runner)
    rows = backend.list_hals("s")
    assert any(row.kind == "aidl" for row in rows)
    assert any(row.pid == 536 and row.process == "codec_service" for row in rows)


def test_process_detail_missing_and_permissions():
    runner = FakeRunner.from_fixtures()
    backend = fake_backend(System, runner)
    assert backend.process_detail("s", 1).fd_count is None
    runner.add(lambda args: "echo ==STATUS" in args[-1], (44, "", ""))
    with pytest.raises(api.CommandFailed, match="不存在"):
        backend.process_detail("s", 999999)
    runner.add(("shell", "kill -9 1"), (1, "", "Operation not permitted"))
    with pytest.raises(api.PermissionDenied):
        backend.kill_process("s", 1)
    with pytest.raises(api.CommandFailed):
        backend.kill_process("s", -1)


def test_init_restart_and_user_fallback():
    runner = FakeRunner.from_fixtures().add(("shell", "stop 'name 空'"), (0, "", "")).add(("shell", "start 'name 空'"), (0, "", ""))
    backend = fake_backend(System, runner)
    backend.control_init_service("s", "name 空", "restart")
    assert [args[-1] for args in runner.calls[-2:]] == ["stop 'name 空'", "start 'name 空'"]
    runner.add(("shell", "cmd user list -v"), (1, "Unknown command", ""))
    assert backend.list_users("s")[0].id == 0
    assert backend.list_init_services("s")
