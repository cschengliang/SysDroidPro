import subprocess
import time

import pytest

from app.backend._base import BackendBase
from app.backend._devices import DeviceMethods
from app.backend import api
from tests.fakes import FakeRunner, fake_backend


class Devices(DeviceMethods, BackendBase):
    pass


def test_inventory_and_cache():
    backend = fake_backend(Devices)
    assert backend.list_devices()[0].api == 36
    count = sum("getprop ro.product.manufacturer;" in args[-1] for args in backend._runner.calls)
    assert backend.list_devices()[0].api == 36
    assert sum("getprop ro.product.manufacturer;" in args[-1] for args in backend._runner.calls) == count
    backend._runner.add(("devices", "-l"), (0, "emulator-5554 offline\n", ""))
    assert backend.list_devices()[0].api is None


def test_slow_device_does_not_block_others():
    runner = FakeRunner.from_fixtures().add(("devices", "-l"), (0, "slow device\nfast device\n", ""))
    def slow(args):
        time.sleep(1.3)
        raise subprocess.TimeoutExpired(args, 2)
    runner.add(lambda a: "slow" in a and "getprop ro.product.manufacturer;" in a[-1], slow)
    backend = fake_backend(Devices, runner)
    start = time.monotonic()
    result = backend.list_devices()
    assert time.monotonic() - start < 1
    assert result[0].api is None and result[1].api == 36


def test_root_wait_and_screenshot():
    backend = fake_backend(Devices)
    backend.root("s")
    assert backend._runner.calls[-1][1:] == ["-s", "s", "wait-for-device"]
    assert backend.root_state("s").adbd_root
    backend.root("s", False)
    assert not backend.root_state("s").adbd_root
    assert backend.screenshot("s").startswith(b"\x89PNG")


def test_root_remount_and_connect_failures():
    runner = FakeRunner().add(("root",), (0, "adbd cannot run as root in production builds", ""))
    backend = fake_backend(Devices, runner)
    with pytest.raises(api.CommandFailed, match="user"):
        backend.root("s")
    runner.add(("remount",), (1, "Not running as root", ""))
    with pytest.raises(api.PermissionDenied):
        backend.remount("s")
    runner.add(("remount",), (1, "Device must be started with -writable-system", ""))
    with pytest.raises(api.CommandFailed, match="writable-system"):
        backend.remount("s")
    runner.add(("remount",), (0, "Now reboot your device for settings to take effect", ""))
    assert backend.remount("s").needs_reboot
    runner.add(("connect", "host:5555"), (0, "failed to connect to host:5555", ""))
    with pytest.raises(api.CommandFailed):
        backend.connect("host")
    runner.add(("connect", "host:5555"), (0, "already connected to host:5555", ""))
    assert backend.connect("host") == "host:5555"


def test_adb_always_scoped_and_shell_nonzero():
    runner = FakeRunner().add(("shell", "false"), (1, "", "failure"))
    backend = fake_backend(Devices, runner)
    assert backend.shell("serial", "false").exit_code == 1
    assert runner.calls[-1] == ["adb", "-s", "serial", "shell", "false"]
    runner.add(("shell", "false"), subprocess.TimeoutExpired("adb", 1))
    with pytest.raises(api.CommandTimeout):
        backend.shell("serial", "false", 1)
