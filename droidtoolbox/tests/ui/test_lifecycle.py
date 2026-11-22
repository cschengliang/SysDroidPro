from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest

from app.backend.api import CommandFailed
from app.backend.mock import MockBackend
from app.backend.models import (
    AndroidUser, Device, DeviceInventory, InventorySource, PackageInfo, RemountResult, RootState, ScreenrecordResult,
)
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
    assert stopped.is_set() and state.jobs.closed



@pytest.mark.parametrize(("saving", "fails"), [(False, False), (True, False), (False, True)])
def test_close_waits_for_recording_save(qapp, tmp_path, saving, fails):
    from tbui._qt import QtCore

    from app.main_window import MainWindow

    stop_requested = threading.Event()
    finish_save = threading.Event()
    save_finished = threading.Event()
    backend_closed = threading.Event()

    class Backend(MockBackend):
        def start_screenrecord(self, serial, dest_path, options, on_exit):
            def save():
                if stop_requested.wait(5) and finish_save.wait(5):
                    save_finished.set()
                    if fails:
                        on_exit(None, CommandFailed("拉取录屏超时"))
                    else:
                        on_exit(ScreenrecordResult(dest_path, 1024, None), None)

            worker = threading.Thread(target=save, daemon=True)
            worker.start()
            return SimpleNamespace(running=True, stop=stop_requested.set)

        def close(self):
            backend_closed.set()

    state = make_state(tmp_path, Backend())
    state.settings.set("export_dir", str(tmp_path))
    window = MainWindow(state)
    window.show()
    capture = state.log_capture
    try:
        assert capture.start_screenrecord("A")
        if saving:
            capture.stop_screenrecord()
        assert not window.close()
        assert not window.close()
        assert window.statusBar().currentMessage() == "正在保存录屏"
        assert window.isVisible() and not state.jobs.closed and not backend_closed.is_set()
        assert stop_requested.is_set()
        event_loop_alive = threading.Event()
        QtCore.QTimer.singleShot(0, event_loop_alive.set)
        assert wait_until(qapp, event_loop_alive.is_set)
        assert not save_finished.is_set()
        finish_save.set()

        if fails:
            assert wait_until(qapp, lambda: window.statusBar().currentMessage() == "录屏保存失败")
            assert window.isVisible() and window.centralWidget().isEnabled()
            assert not state.jobs.closed and not backend_closed.is_set()
            assert window.close()
        else:
            assert wait_until(qapp, lambda: not window.isVisible())
            assert save_finished.is_set() and capture.last_video is not None
        assert state.jobs.closed and backend_closed.is_set()
    finally:
        finish_save.set()
        state.shutdown()
        window.close()
        window.deleteLater()
        qapp.sendPostedEvents(window, QtCore.QEvent.Type.DeferredDelete)


def test_remount_resumes_on_fresh_scan_of_new_transport(qapp, tmp_path, monkeypatch):
    import tbui as ui

    from app.pages.devices import DevicesPage

    before_reboot = replace(A, connection_id="after-root")
    reconnected = replace(A, connection_id="after-reboot")
    inventory = DeviceInventory((A, B))
    remounts = []

    class Backend(MockBackend):
        def scan_inventory(self):
            return inventory

        def root_state(self, serial):
            return RootState(True, False, "userdebug", False)

        def remount(self, serial):
            remounts.append(serial)
            return RemountResult("adb remount", needs_reboot=len(remounts) == 1)

        def reboot(self, serial, mode="system"):
            return before_reboot

    state = make_state(tmp_path, Backend())
    page = DevicesPage(Context(state, SimpleNamespace(update_status=lambda _: None)))
    monkeypatch.setattr(ui, "confirm", lambda *args, **kwargs: True)
    try:
        state._set_devices([A, B])
        page.set_visible_page(True)
        page._remount_flow(A.serial)
        assert wait_until(qapp, lambda: page._pending_remount == (A.serial, before_reboot.connection_id))

        def refresh(devices, status):
            nonlocal inventory
            inventory = DeviceInventory(devices, (InventorySource("adb", status),))
            state.refresh_devices()
            assert wait_until(qapp, lambda: not state._polling)

        refresh((before_reboot, B), "ready")
        state.select(B.serial)
        refresh((reconnected, B), "error")
        assert page._pending_remount is not None
        assert remounts == [A.serial]

        refresh((reconnected, B), "ready")
        assert wait_until(qapp, lambda: remounts == [A.serial, A.serial] and not page._busy)
        assert page._pending_remount is None and state.current == B
    finally:
        state.shutdown()


def test_device_overview_switch_keeps_widgets_embedded(qapp, tmp_path):
    import tbui as ui
    from tbui._qt import QtCore

    from app.pages.devices import DevicesPage

    promoted = []

    class WindowWatch(QtCore.QObject):
        def eventFilter(self, widget, event):
            if (
                event.type() == QtCore.QEvent.Type.ParentChange
                and isinstance(widget, (ui.Terminal, ui.ProgressRow))
                and widget.isWindow()
            ):
                promoted.append(type(widget).__name__)
            return False

    backend = MockBackend()
    devices = [device for device in backend.list_devices() if device.is_adb][:2]
    assert len(devices) == 2
    state = make_state(tmp_path, backend)
    page = DevicesPage(Context(state, SimpleNamespace(update_status=lambda _: None)))
    output = page._log
    output.write("retained output")
    watch = WindowWatch()
    qapp.installEventFilter(watch)
    try:
        page.show()
        page.set_visible_page(True)
        state._set_devices(devices)
        for index in (0, 1, 0):
            page.list.setCurrentRow(index)
            assert wait_until(qapp, lambda: page.detail is not None and page.detail.serial == devices[index].serial)
            assert page._log is output and output.isVisible()
            assert page.scroll.widget().isAncestorOf(output)

        state._set_devices([])
        assert output.isHidden()
        assert page.scroll.widget().isAncestorOf(output)

        state._set_devices(devices)
        assert wait_until(qapp, lambda: page.detail is not None)
        assert output.isVisible() and "retained output" in output.out.toPlainText()
        assert promoted == []
    finally:
        qapp.removeEventFilter(watch)
        page.close()
        state.shutdown()
