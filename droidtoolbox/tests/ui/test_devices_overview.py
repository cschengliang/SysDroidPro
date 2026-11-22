"""设备总览页（devices）4 卡片精简布局与交互离屏测试（MockBackend）。"""
from __future__ import annotations

import sys
import traceback

import pytest
import tbui
from tbui._qt import QtCore, QtGui, QtWidgets, Qt

from app.backend.mock import MockBackend
from app.pages.devices import _InfoRow, _MetricRow
from app.ui.common import PanelHead
from tests.conftest import pump, wait_until

pytestmark = pytest.mark.ui
SERIAL = "3A281FDH2000XY"


@pytest.fixture(scope="module")
def win(qapp, tmp_path_factory):
    import app.ui.snapshot as snapshot
    from app.core.commands import CommandStore
    from app.core.settings import Settings
    from app.core.state import AppState
    from app.main_window import MainWindow

    tmp = tmp_path_factory.mktemp("devices_overview_ui")
    old_dir, snapshot.DATA_DIR = snapshot.DATA_DIR, tmp
    errors: list[str] = []
    old_hook = sys.excepthook
    sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))

    s = Settings(tmp / "settings.ini")
    s.set("export_dir", str(tmp / "export"))
    s.set("last_page", "devices")
    st = AppState(MockBackend(), s, CommandStore(tmp / "commands.json"))
    w = MainWindow(st)
    w.resize(1360, 860)
    w.show()
    st.start()
    pump(qapp, 0.8)
    w.errors = errors
    yield w
    w.close()
    pump(qapp, 0.2)
    sys.excepthook = old_hook
    snapshot.DATA_DIR = old_dir


@pytest.fixture(autouse=True)
def no_callback_errors(win, monkeypatch):
    monkeypatch.setattr(tbui, "confirm", lambda *a, **k: True)
    win.errors.clear()
    yield
    assert not win.errors, "回调中出现未处理异常：\n" + "\n".join(win.errors)


def _open_device(win, qapp, serial: str = SERIAL):
    win.state.select(serial)
    win.open_page("devices")
    dp = win.pages["devices"]
    pump(qapp, 0.2)
    return dp


def _click_frame(w: QtWidgets.QWidget):
    pos = QtCore.QPointF(w.rect().center())
    press = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
    )
    release = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonRelease, pos, pos,
        Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
    )
    QtWidgets.QApplication.sendEvent(w, press)
    QtWidgets.QApplication.sendEvent(w, release)


def test_four_cards_and_on_demand_output(win, qapp):
    dp = _open_device(win, qapp, SERIAL)
    assert wait_until(qapp, lambda: dp.detail is not None and dp.root is not None, 5)

    card_titles = {h.title.text() for h in dp.findChildren(PanelHead)}
    assert {"常用操作", "系统与安全", "硬件规格", "刷机版本信息"}.issubset(card_titles)
    assert "Mainline / APEX 模块" not in card_titles
    assert "运行资源" not in card_titles
    assert not hasattr(dp, "apex_table") and not hasattr(dp, "reboot_btn")

    assert dp.bat_badge.isVisibleTo(dp) and "%" in dp.bat_badge.text()
    assert dp.detail.fingerprint in dp.fp_lbl.text()

    # 初始未执行命令时，输出区隐藏；点 Root/Unroot 后自动展开记录，清空后重新隐藏
    assert not dp._output_wrap.isVisible()
    was_root = dp.root.adbd_root
    dp.root_btn.click()
    assert wait_until(qapp, lambda: dp.root is not None and dp.root.adbd_root is (not was_root), 5)
    assert dp._output_wrap.isVisibleTo(dp) and dp._log.isVisibleTo(dp)
    assert ("adb unroot" if was_root else "adb root") in dp._log.out.toPlainText()

    # 恢复原 root 状态并清空输出
    dp.root_btn.click()
    assert wait_until(qapp, lambda: dp.root is not None and dp.root.adbd_root is was_root, 5)
    dp._output_clear_btn.click()
    pump(qapp, 0.1)
    assert not dp._output_wrap.isVisible()


def test_copy_buttons_and_clickable_rows(win, qapp):
    dp = _open_device(win, qapp, SERIAL)
    assert wait_until(qapp, lambda: dp.detail is not None, 5)

    dp.serial_btn.click()
    assert qapp.clipboard().text() == SERIAL

    dp.copy_fp_btn.click()
    assert qapp.clipboard().text() == dp.detail.fingerprint

    dp.copy_flash_btn.click()
    flash_clip = qapp.clipboard().text()
    assert "Build ID:" in flash_clip and "编译时间:" in flash_clip and "基带版本:" in flash_clip
    assert dp.detail.build_date in flash_clip

    dp.copy_summary_btn.click()
    summary_clip = qapp.clipboard().text()
    assert "系统与安全:" in summary_clip and "硬件规格:" in summary_clip
    assert "刷机版本:" in summary_clip and "内存 " in summary_clip and "磁盘 " in summary_clip

    soc_row = next(r for r in dp.findChildren(_InfoRow) if r.key == "CPU / SoC")
    _click_frame(soc_row)
    assert qapp.clipboard().text() == dp.detail.soc

    ram_row = next(r for r in dp.findChildren(_MetricRow) if "内存" in r.title)
    _click_frame(ram_row)
    assert "GB" in qapp.clipboard().text()


def test_user_wifi_unauthorized_and_fastboot_states(win, qapp):
    # Wi-Fi + user 固件设备
    dp = _open_device(win, qapp, "192.168.1.23:5555")
    assert wait_until(qapp, lambda: dp.detail is not None and dp.detail.serial == "192.168.1.23:5555" and dp.root is not None, 5)
    assert not dp.root_btn.isEnabled() and not dp.remount_btn.isEnabled()
    assert "user build" in dp.root_state_lbl.text()
    assert dp.disconnect_btn is not None and dp.disconnect_btn.isVisibleTo(dp)

    # 未授权设备
    dp = _open_device(win, qapp, "R5CW20ABCDE")
    pump(qapp, 0.3)
    assert dp.detail is None
    assert not dp.root_btn.isEnabled() and not dp.remount_btn.isEnabled()
    assert not dp.copy_summary_btn.isVisible() and not dp.bat_badge.isVisible()

    # Fastboot 设备
    dp = _open_device(win, qapp, "28011FDH30016K")
    pump(qapp, 0.3)
    assert dp.detail is None
    assert not dp.root_btn.isEnabled() and not dp.remount_btn.isEnabled()
    assert not dp.copy_summary_btn.isVisible()

    # 切回在线设备并切换主题验证重绘无异常
    dp = _open_device(win, qapp, SERIAL)
    assert wait_until(qapp, lambda: dp.detail is not None, 5)
    tbui.theme().toggle()
    pump(qapp, 0.25)
    assert tbui.theme().name == "dark"
    tbui.theme().toggle()
    pump(qapp, 0.15)
    assert tbui.theme().name == "light"



def test_memory_without_available_does_not_show_full_usage(win, qapp, monkeypatch):
    from dataclasses import replace

    dp = _open_device(win, qapp)
    backend = win.state.backend
    original = backend.device_detail

    def without_mem_available(serial):
        return replace(original(serial), mem_available=None)

    monkeypatch.setattr(backend, "device_detail", without_mem_available)
    dp.refresh()
    assert wait_until(qapp, lambda: dp.detail is not None and dp.detail.mem_available is None, 5)
    assert not any("内存" in row.title for row in dp.findChildren(_MetricRow))
    row = next(row for row in dp.findChildren(_InfoRow) if row.key == "内存 (RAM)")
    assert row.copy_value.startswith("总计 ")
    assert row.copy_value in dp._summary_text


def test_pending_snapshot_is_reused_until_manual_refresh(win, qapp, monkeypatch):
    import threading

    backend = win.state.backend
    read_detail = backend.device_detail
    started = threading.Event()
    release = threading.Event()
    reads = []

    def slow_detail(serial):
        reads.append(serial)
        started.set()
        assert release.wait(5)
        return read_detail(serial)

    monkeypatch.setattr(backend, "device_detail", slow_detail)
    win.open_page("devices")
    dp = win.pages["devices"]
    try:
        # Force a new selection while the overview is visible.
        win.state.select("192.168.1.23:5555")
        assert wait_until(qapp, started.is_set)
        win.open_page("logcat")
        win.open_page("devices")
        pump(qapp, 0.15)
        assert reads == ["192.168.1.23:5555"]
        release.set()

        assert wait_until(qapp, lambda: dp.detail is not None)
        snapshot = dp.detail
        win.state.refresh_devices()
        assert wait_until(qapp, lambda: not win.state._polling)
        assert dp.detail is snapshot and len(reads) == 1

        dp.refresh()
        assert wait_until(qapp, lambda: dp.detail is not None and dp.detail is not snapshot)
        assert reads == ["192.168.1.23:5555"] * 2
    finally:
        release.set()
        win.state.select(SERIAL)
