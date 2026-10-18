import shlex
import threading

import pytest

from app.backend import api
from app.backend._apps import AppMethods
from app.backend._base import BackendBase
from app.backend._jobs import JobMethods
from app.backend._system import SystemMethods
from app.backend.models import InstallOptions
from tests.fakes import FakeProcess, FakeRunner, fake_backend


class Apps(AppMethods, JobMethods, SystemMethods, BackendBase):
    pass


def test_packages_and_details():
    backend = fake_backend(Apps)
    assert backend.list_packages("s")
    assert backend.package_detail("s", "com.android.settings").info.system
    assert backend.package_components("s", "com.example.helloworld")
    with pytest.raises(api.CommandFailed, match="找不到应用"):
        backend.package_detail("s", "no.such.package")


def test_install_options_and_translated_failure():
    runner, process = FakeRunner(), FakeProcess()
    runner.processes.append(process)
    process.stdout.feed("Success\n")
    process.finish()
    backend = fake_backend(Apps, runner)
    backend.install("s", ["C:/one apk.apk", "C:/split.apk"], InstallOptions(True, True, True, True, 10))
    assert runner.calls[-1] == ["adb", "-s", "s", "install-multiple", "-r", "-d", "-g", "-t", "--user", "10", "C:/one apk.apk", "C:/split.apk"]
    process = FakeProcess()
    runner.processes.append(process)
    process.stderr.feed("Failure [INSTALL_FAILED_VERSION_DOWNGRADE]\n")
    process.finish(1)
    with pytest.raises(api.CommandFailed, match="降级"):
        backend.install("s", ["a.apk"], InstallOptions())


def test_install_cancel_quiet_process():
    runner, cancel, progress = FakeRunner(), threading.Event(), []
    backend = fake_backend(Apps, runner)
    def tick(p):
        progress.append(p)
        if len(progress) >= 3:
            cancel.set()
    with pytest.raises(api.Cancelled):
        backend.install("s", ["a.apk"], InstallOptions(), tick, cancel)
    assert runner.spawned[0].terminated
    assert all(p.percent is None for p in progress)


@pytest.mark.parametrize("method,args,want", [
    ("uninstall", ("pkg", True, 10), ["pm", "uninstall", "-k", "--user", "10", "pkg"]),
    ("clear_data", ("pkg", 10), ["pm", "clear", "--user", "10", "pkg"]),
    ("force_stop", ("pkg", 10), ["am", "force-stop", "--user", "10", "pkg"]),
    ("set_enabled", ("pkg", False, 10), ["pm", "disable-user", "--user", "10", "pkg"]),
    ("set_component_enabled", ("pkg/.Cls", False, 10), ["pm", "disable", "--user", "10", "pkg/.Cls"]),
    ("set_permission", ("pkg", "android.permission.CAMERA", True, 10), ["pm", "grant", "--user", "10", "pkg", "android.permission.CAMERA"]),
])
def test_operations_user_arguments(method, args, want):
    runner = FakeRunner().add(lambda args: True, (0, "Success\nnew state: disabled", ""))
    backend = fake_backend(Apps, runner)
    getattr(backend, method)("s", *args)
    assert shlex.split(runner.calls[-1][-1]) == want


def test_launch_resolution_and_no_activity():
    runner = FakeRunner.from_fixtures().add(("shell", "am start --user 10 -n pkg/.MainActivity"), (0, "Starting", ""))
    backend = fake_backend(Apps, runner)
    backend.launch("s", "pkg", 10)
    assert runner.calls[-1][-1] == "am start --user 10 -n pkg/.MainActivity"
    runner.add(lambda args: "resolve-activity" in args[-1], (0, "No activity found", ""))
    with pytest.raises(api.CommandFailed, match="没有可启动"):
        backend.launch("s", "pkg")
