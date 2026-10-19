from pathlib import Path

import pytest

from app.backend import api
from app.backend import parsers as P
from app.backend.models import InstallOptions

pytestmark = [pytest.mark.device, pytest.mark.destructive]
PACKAGE = "com.droidtoolbox.backendtest"
CAMERA = "android.permission.CAMERA"
APK = Path(__file__).parents[1] / "fixtures/apk/backend-test.apk"


def test_ungranted_runtime_camera_can_be_granted_and_revoked(device_backend, serial):
    backend = device_backend
    if any(row.name == PACKAGE for row in backend.list_packages(serial)):
        pytest.skip("测试包已由用户安装，保留原状")
    before = backend.root_state(serial)

    def camera():
        return next(row for row in backend.package_detail(serial, PACKAGE).permissions if row.name == CAMERA)

    try:
        # No -g: CAMERA must initially be absent from the user's runtime section.
        backend.install(serial, [str(APK)], InstallOptions(user=0))
        assert CAMERA in P.pending_permissions(backend.dump_package(serial, PACKAGE), PACKAGE)
        initial = camera()
        assert initial.runtime and not initial.granted
        backend.set_permission(serial, PACKAGE, CAMERA, True)
        granted = camera()
        assert granted.runtime and granted.granted
        backend.set_permission(serial, PACKAGE, CAMERA, False)
        revoked = camera()
        assert revoked.runtime and not revoked.granted
    finally:
        if any(row.name == PACKAGE for row in backend.list_packages(serial)):
            backend.uninstall(serial, PACKAGE)
        assert not any(row.name == PACKAGE for row in backend.list_packages(serial))
        with pytest.raises(api.CommandFailed):
            backend.package_detail(serial, PACKAGE)
        after = backend.root_state(serial)
        assert (after.adbd_root, after.system_writable, after.verity_enabled) == (
            before.adbd_root, before.system_writable, before.verity_enabled
        )
