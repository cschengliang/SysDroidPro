"""AppState 当前设备的自动选择规则（不需要设备）。"""
from __future__ import annotations

import pytest

from app.backend.mock import MockBackend
from app.backend.models import Device

pytestmark = pytest.mark.ui

A = Device("A", "device", "usb", "Pixel A", "Google")
B = Device("B", "device", "usb", "Pixel B", "Google")
C = Device("C", "device", "usb", "Pixel C", "Google")


@pytest.fixture
def state(qapp, tmp_path):
    from app.core.commands import CommandStore
    from app.core.settings import Settings
    from app.core.state import AppState
    s = Settings(tmp_path / "s.ini")
    s.set("auto_root", False)
    return AppState(MockBackend(), s, CommandStore(tmp_path / "c.json"))


def test_first_device_selected(state):
    state._set_devices([A])
    assert state.serial == "A"


def test_unplugged_then_new_device_is_selected(state):
    state._set_devices([A])
    state._set_devices([])
    assert state.current is None
    state._set_devices([B])
    assert state.serial == "B", "选中的设备拔掉后，新插入的设备应自动成为当前设备"


def test_rebooting_device_keeps_selection(state):
    """A 重启期间短暂消失，B 一直在线：不切到 B；A 回来后仍是 A。"""
    state._set_devices([A, B])
    state.select("A")
    state._set_devices([B])
    assert state.current is None
    state._set_devices([A, B])
    assert state.serial == "A"


def test_new_device_while_selected_missing(state):
    state._set_devices([A, B])
    state.select("A")
    state._set_devices([B])
    state._set_devices([B, C])
    assert state.serial == "C"
