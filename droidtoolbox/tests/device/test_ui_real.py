import sys
import traceback

import pytest

from tests.conftest import pump, wait_until

pytestmark = [pytest.mark.device, pytest.mark.ui]


def test_all_pages_real_backend(qapp, device_backend, serial, tmp_path, monkeypatch):
    from app.core.commands import CommandStore
    from app.core.settings import Settings
    from app.core.state import AppState
    from app.main_window import MainWindow
    from tbui._qt import QtCore, QtWidgets
    import app.ui.snapshot as snapshot
    settings = Settings(tmp_path / "settings.ini")
    settings.set("auto_root", False)
    settings.set("export_dir", str(tmp_path / "export"))
    settings.set("last_page", "devices")
    monkeypatch.setattr(snapshot, "DATA_DIR", tmp_path)
    errors, notices = [], []
    monkeypatch.setattr(sys, "excepthook", lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb))))
    state = AppState(device_backend, settings, CommandStore(tmp_path / "commands.json"))
    state.notified.connect(lambda *args: notices.append(args))
    window = MainWindow(state)
    window.show()
    try:
        state.start()
        assert wait_until(qapp, lambda: state.current is not None, 5)
        state.select(serial)
        assert len(window.pages) == 13 and state.backend.name == "adb"
        assert not any(label.text() == "MOCK" for label in window.findChildren(QtWidgets.QLabel))
        for pid in window.pages:
            window.open_page(pid)
            pump(qapp, .7)
            assert window.stack.currentWidget() is window.pages[pid]
            assert window.grab().save(str(tmp_path / f"real-{pid}.png"))
        window.open_page("services")
        for key, table in (("binder", "b_table"), ("init", "i_table"), ("hal", "h_table"), ("proc", "p_table")):
            window.pages["services"].tabs.seg.set_value(key)
            assert wait_until(qapp, lambda: getattr(window.pages["services"], table).table_model.rowCount() > 0, 5)
        window.open_page("devices")
        assert wait_until(qapp, lambda: window.pages["devices"].detail is not None, 5)
        QtCore.QThreadPool.globalInstance().waitForDone(5000)
        pump(qapp, .2)
        assert not errors, "\n".join(errors)
        assert not [item for item in notices if item[0] == "danger"], notices
    finally:
        window.close()
        state._timer.stop()
        pump(qapp, .3)
