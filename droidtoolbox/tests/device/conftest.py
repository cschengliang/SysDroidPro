import os

import pytest

from app.backend.adb import AdbBackend


@pytest.fixture
def serial():
    value = os.environ.get("DTB_DEVICE")
    if not value:
        pytest.skip("未设置 DTB_DEVICE")
    return value


@pytest.fixture
def device_backend(serial):
    backend = AdbBackend()
    assert any(device.serial == serial and device.is_adb for device in backend.list_devices())
    yield backend
    backend._pool.shutdown(wait=True)


@pytest.fixture
def rooted(device_backend, serial):
    before = device_backend.root_state(serial)
    try:
        if not before.adbd_root:
            device_backend.root(serial)
        yield device_backend
    finally:
        if device_backend.root_state(serial).adbd_root != before.adbd_root:
            device_backend.root(serial, before.adbd_root)
        after = device_backend.root_state(serial)
        assert (after.adbd_root, after.system_writable, after.verity_enabled) == (before.adbd_root, before.system_writable, before.verity_enabled)
