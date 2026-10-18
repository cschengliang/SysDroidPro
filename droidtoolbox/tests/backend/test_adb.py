import inspect
import threading
import time

import pytest

from app.backend import api, create_backend
from app.backend.adb import AdbBackend
from app.backend.models import ScrcpyOptions
from tests.fakes import fake_backend


def test_concrete_exact_signatures_and_factory():
    backend = fake_backend(AdbBackend)
    assert not AdbBackend.__abstractmethods__
    assert create_backend().name == "adb"
    for name in api.Backend.__abstractmethods__:
        assert list(inspect.signature(getattr(AdbBackend, name)).parameters) == list(inspect.signature(getattr(api.Backend, name)).parameters)
    assert backend.name == "adb"


def test_scrcpy_options_environment_and_restore():
    backend = fake_backend(AdbBackend)
    runner = backend._runner
    before = backend.list_settings("s", "system").get("show_touches")
    done, errors = threading.Event(), []
    options = ScrcpyOptions(max_size=None, max_fps=None, bit_rate_mbps=12, stay_awake=True, turn_screen_off=True,
        show_touches=True, always_on_top=True, audio=False, record_path="C:/a 空.mp4", window_title="中文 ' title",
        window_x=-10, window_y=20, window_width=800, window_height=600)
    handle = backend.start_scrcpy("s", options, lambda *args: None, lambda err: (errors.append(err), done.set()))
    deadline = time.monotonic() + 2
    while not runner.spawned and time.monotonic() < deadline:
        time.sleep(.01)
    assert runner.spawned
    args = runner.calls[-1]
    assert args[:3] == ["scrcpy", "-s", "s"]
    assert "--video-bit-rate=12M" in args and "--no-audio" in args
    assert "--window-title=中文 ' title" in args and "--record=C:/a 空.mp4" in args and "--window-x=-10" in args
    assert not any(arg.startswith("--max-size=") or arg.startswith("--max-fps=") for arg in args)
    assert runner.environments[-1]["ADB"] == "adb"
    runner.settings["system", 0]["show_touches"] = "1"
    handle.stop()
    assert done.wait(2) and errors == [None]
    assert backend.list_settings("s", "system").get("show_touches") == before


def test_missing_scrcpy_and_unknown_device():
    backend = fake_backend(AdbBackend)
    backend._paths["scrcpy"] = None
    with pytest.raises(api.ToolNotFound):
        backend.start_scrcpy("s", ScrcpyOptions(), lambda *args: None, lambda err: None)
    with pytest.raises(api.DeviceNotFound):
        backend.device_detail("no-such-serial")


def test_scrcpy_startup_queries_are_asynchronous():
    backend = fake_backend(AdbBackend)
    runner = backend._runner
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    def slow(args):
        entered.set()
        release.wait(1)
        return 0, "stay_on_while_plugged_in=0", ""
    runner.add(("shell", "settings list global"), slow)
    started = time.monotonic()
    handle = backend.start_scrcpy("s", ScrcpyOptions(), lambda *args: None, lambda error: done.set())
    assert time.monotonic() - started < .2 and entered.wait(1)
    handle.stop()
    release.set()
    assert done.wait(2) and not runner.spawned
