from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest

from app.backend.mock import MockBackend
from app.backend.models import AndroidUser, Device, PackageInfo
from app.core.commands import CommandStore
from app.core.jobs import JobManager
from app.core.settings import Settings
from app.core.state import AppState, Context
from app.pages.apps import AppsPage
from tests.conftest import wait_until

pytestmark = pytest.mark.ui
A = Device("A", "device", "usb", connection_id="1")
B = Device("B", "device", "usb", connection_id="2")


def make_state(tmp_path, backend):
    settings = Settings(tmp_path / "settings.ini")
    settings.set("auto_root", False)
    return AppState(backend, settings, CommandStore(tmp_path / "commands.json"))


@pytest.mark.parametrize("reconnect", [False, True])
def test_apps_rejects_late_packages_for_previous_connection(qapp, tmp_path, reconnect):
    started, release = threading.Event(), threading.Event()

    class Backend(MockBackend):
        count = 0

        def list_users(self, serial):
            return [AndroidUser(0, "Owner", "Owner", True)]

        def list_packages(self, serial, user=0):
            self.count += 1
            if self.count == 1:
                started.set()
                assert release.wait(10)
                name = "old.package"
            else:
                name = "new.package"
            return [PackageInfo(name, None, None, False, True, None)]

    state = make_state(tmp_path, Backend())
    page = AppsPage(Context(state, SimpleNamespace(update_status=lambda p: None)))
    names = lambda: [p.name for p in page.table.table_model.rows]
    try:
        state._set_devices([A, B])
        page.set_visible_page(True)
        assert wait_until(qapp, started.is_set)
        if reconnect:
            state._set_devices([replace(A, connection_id="3"), B])
        else:
            state.select("B")
        assert wait_until(qapp, lambda: names() == ["new.package"])
        release.set()
        assert wait_until(qapp, lambda: page.busy._n == 0)
        assert names() == ["new.package"]
    finally:
        release.set()
        state.shutdown()


def test_shutdown_cancels_work_and_stops_late_stream(qapp):
    manager = JobManager(max_workers=1)
    started, release, stopped = threading.Event(), threading.Event(), threading.Event()
    seen = []
    stream = SimpleNamespace(running=True, stop=stopped.set)

    def launch(cancel):
        started.set()
        assert release.wait(5)
        assert cancel.is_set()
        return manager.own_stream(stream)

    try:
        active = manager.run(launch, cancellable=True, done=seen.append)
        assert started.wait(2)
        pending = manager.run(lambda: seen.append("ran"))
        callback = manager.on_ui(seen.append)
        manager.close()
        callback("late output")
        release.set()
        assert stopped.wait(2)
        active._future.result(2)
        qapp.processEvents()
        assert pending._future.cancelled() and active.finished
        assert manager.run(lambda: seen.append("after shutdown")).finished
        assert seen == []
    finally:
        release.set()
        manager.close()


def test_close_window_stops_command_even_without_a_device_page(qapp, tmp_path):
    from app.main_window import MainWindow

    state = make_state(tmp_path, MockBackend())
    window = MainWindow(state)
    stopped = threading.Event()
    state.jobs.own_stream(SimpleNamespace(running=True, stop=stopped.set))
    assert window.close()
    assert stopped.is_set() and state.jobs.closed and not state._timer.isActive()
