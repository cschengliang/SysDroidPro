"""日志抓取页（log_capture）与扩展屏幕录制离屏交互测试（MockBackend）。"""
from __future__ import annotations

import dataclasses
import re
import sys
import threading
import traceback
from pathlib import Path
from types import SimpleNamespace

import pytest
import tbui
from tbui._qt import QtWidgets

from app.backend.mock import MockBackend
from app.backend.models import DeviceInventory, InventorySource
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

    tmp = tmp_path_factory.mktemp("log_capture_ui")
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
    w.shots = tmp
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


def _open_log_capture(win, qapp):
    win.state.select(SERIAL)
    win.open_page("log_capture")
    lp = win.pages["log_capture"]
    wait_until(qapp, lambda: lp.device is not None and "MB" in lp.size_readout.text(), timeout=4.0)
    return lp


def test_default_state_and_collapsed_video(win, qapp):
    lp = _open_log_capture(win, qapp)
    ctrl = win.state.log_capture

    assert not lp.video_body.isVisible()
    assert ctrl.selected_buffers() == ["main", "system", "crash"]
    assert lp.sw_auto.isChecked() is False
    assert win.state.settings.get("log_capture_auto") is False
    assert lp.sw_clear_before.isChecked() is False
    assert lp.status_badge.text() == "待机"
    assert "16 MB" in lp.size_readout.text()


def test_buffer_multiselect_and_size_clamping(win, qapp):
    lp = _open_log_capture(win, qapp)
    ctrl = win.state.log_capture

    # 多选与恢复默认
    lp.buf_buttons["events"].click()
    assert ctrl.selected_buffers() == ["main", "system", "crash", "events"]
    assert win.state.settings.get("log_capture_buffers") == "main,system,crash,events"

    lp.select_all_btn.click()
    assert ctrl.selected_buffers() == ["main", "system", "crash", "events", "radio", "kernel"]
    assert lp.select_all_btn.text() == "恢复默认"

    # 32 MB / 128 MB（触发 kernel 上限 64 MB 提示）
    lp.size_buttons[32].click()
    wait_until(qapp, lambda: lp.size_buttons[32].isChecked() and "32 MB" in lp.size_readout.text())

    lp.size_buttons[128].click()
    wait_until(qapp, lambda: "受限" in lp.size_readout.text() and "kernel 64M" in lp.size_readout.text())

    # 恢复默认缓冲区，设为 256 MB 并刷新 -g
    lp.select_all_btn.click()
    assert ctrl.selected_buffers() == ["main", "system", "crash"]
    assert lp.select_all_btn.text() == "全选"

    lp.size_buttons[256].click()
    wait_until(qapp, lambda: lp.size_buttons[256].isChecked() and lp.size_readout.text() == "当前: 256 MB")
    lp.refresh_g_btn.click()
    pump(qapp, 0.25)
    assert lp.size_readout.text() == "当前: 256 MB"

    # 清空勾选会禁用抓取 / 导出 / 清理按钮并提示
    for name in ("main", "system", "crash"):
        lp.buf_buttons[name].click()
    assert ctrl.selected_buffers() == []
    assert not lp.capture_btn.isEnabled()
    assert not lp.dump_btn.isEnabled()
    assert not lp.clear_btn.isEnabled()
    assert "至少选择 1 个" in lp.size_readout.text()

    # 还原默认（全选 -> 恢复默认）
    lp.select_all_btn.click()
    lp.select_all_btn.click()
    assert ctrl.selected_buffers() == ["main", "system", "crash"]


def test_capture_dump_clear_and_copy_path(win, qapp, monkeypatch):
    import app.pages.log_capture as lc_mod

    revealed: list[str] = []
    monkeypatch.setattr(lc_mod, "reveal", lambda p: revealed.append(p))

    lp = _open_log_capture(win, qapp)
    ctrl = win.state.log_capture

    # 清理缓冲区
    lp.clear_btn.click()
    pump(qapp, 0.25)

    # 开始抓取 -> 停止抓取 -> 按时间戳和缓冲区命名
    lp.capture_btn.click()
    wait_until(qapp, lambda: ctrl.log_recording and ctrl.log_bytes > 0, timeout=4.0)
    assert lp.capture_btn.text() == "停止抓取"
    assert lp.active_log_hint.isVisible()
    assert not lp.buf_buttons["main"].isEnabled()
    assert "录制中" in lp.status_text()

    lp.capture_btn.click()
    wait_until(qapp, lambda: not ctrl.log_recording and ctrl.last_log is not None, timeout=4.0)

    log_path = Path(ctrl.last_log.path)
    assert log_path.is_file() and log_path.stat().st_size > 0
    assert re.fullmatch(r"logcat_\d{8}_\d{6}_main-system-crash(?:-\d+)?\.log", log_path.name)
    assert lp.last_log_path.text() == str(log_path)
    assert "✓ main+sys+crash" in lp.last_log_badge.text()
    assert not lp.active_log_hint.isVisible()

    # 一键复制日志路径 & 打开导出目录
    lp.copy_log_btn.click()
    assert QtWidgets.QApplication.clipboard().text() == str(log_path)
    lp.open_log_dir_btn.click()
    assert revealed and Path(revealed[-1]) == log_path.parent

    # 导出缓存（全部缓冲区 -> dump 文件命名）
    lp.select_all_btn.click()
    lp.dump_btn.click()
    wait_until(
        qapp,
        lambda: ctrl.last_log is not None and "logcat_dump_" in Path(ctrl.last_log.path).name,
        timeout=4.0,
    )
    dump_path = Path(ctrl.last_log.path)
    assert dump_path.is_file()
    assert re.fullmatch(r"logcat_dump_\d{8}_\d{6}_all(?:-\d+)?\.log", dump_path.name)
    assert "--------- beginning of main" in dump_path.read_text(encoding="utf-8")

    # 恢复默认缓冲区
    lp.select_all_btn.click()


def test_screenrecord_collapsed_expand_and_sync(win, qapp):
    lp = _open_log_capture(win, qapp)
    ctrl = win.state.log_capture

    # 默认折叠，点击标题按钮后展开并加载 Display ID
    assert not lp.video_body.isVisible()
    lp.video_expand_btn.click()
    assert lp.video_body.isVisible()
    wait_until(qapp, lambda: lp.display_combo.count() >= 2, timeout=4.0)

    # 参数设置：--bugreport / --bit-rate / --display-id
    lp.display_combo.setCurrentIndex(1)
    idx_12m = lp.bitrate_combo.findData("12000000")
    assert idx_12m >= 0
    lp.bitrate_combo.setCurrentIndex(idx_12m)
    lp.sw_bugreport.click()
    pump(qapp, 0.1)

    assert win.state.settings.get("log_capture_display_id") == "4619827677550801153"
    assert win.state.settings.get("log_capture_bitrate") == 12_000_000
    assert win.state.settings.get("log_capture_bugreport") is False

    # 独立录屏并复制 MP4 路径
    lp.video_btn.click()
    wait_until(qapp, lambda: ctrl.video_recording, timeout=3.0)
    assert lp.video_btn.text() == "停止录屏"
    assert lp.active_video_hint.isVisible()
    assert not lp.sw_bugreport.isEnabled()

    pump(qapp, 0.35)
    lp.video_btn.click()
    wait_until(qapp, lambda: not ctrl.video_recording and ctrl.last_video is not None, timeout=4.0)

    mp4_path = Path(ctrl.last_video.path)
    assert mp4_path.is_file() and mp4_path.stat().st_size > 0
    assert b"ftypisom" in mp4_path.read_bytes()[:32]
    assert re.fullmatch(r"screenrecord_\d{8}_\d{6}(?:-\d+)?\.mp4", mp4_path.name)
    assert lp.last_video_path.text() == str(mp4_path)

    lp.copy_video_btn.click()
    assert QtWidgets.QApplication.clipboard().text() == str(mp4_path)

    # 随日志同步录屏
    if not lp.sw_sync_video.isChecked():
        lp.sw_sync_video.click()
    lp.capture_btn.click()
    wait_until(qapp, lambda: ctrl.log_recording and ctrl.video_recording, timeout=4.0)
    pump(qapp, 0.3)

    prev_video = ctrl.last_video.path
    lp.capture_btn.click()
    wait_until(
        qapp,
        lambda: not ctrl.log_recording and not ctrl.video_recording and ctrl.last_video.path != prev_video,
        timeout=4.0,
    )
    assert Path(ctrl.last_video.path).is_file()

    # 还原折叠与开关状态
    lp.sw_sync_video.click()
    lp.sw_bugreport.click()
    lp.video_expand_btn.click()
    assert not lp.video_body.isVisible()


def test_auto_capture_on_adb_connect_and_disconnect(win, qapp, monkeypatch):
    lp = _open_log_capture(win, qapp)
    ctrl = win.state.log_capture
    base_dev = next(d for d in win.state.devices if d.serial == SERIAL)

    # 开启自动抓取开关时，不劫持当前已在线设备
    if not lp.sw_auto.isChecked():
        lp.sw_auto.click()
    pump(qapp, 0.2)
    assert not ctrl.log_recording

    # 切到其他页面，证明自动抓取全局生效
    win.open_page("devices")

    current_inv = DeviceInventory(devices=(dataclasses.replace(base_dev, connection_id="conn-auto-1"),), sources=())
    monkeypatch.setattr(win.state.backend, "poll_inventory", lambda: current_inv)

    win.state.refresh_devices()
    wait_until(qapp, lambda: ctrl.log_recording and ctrl.log_serial == SERIAL and ctrl.log_bytes > 0, timeout=4.0)

    # 手动停止后，同一 connection_id 刷新设备不会重复触发
    win.open_page("log_capture")
    lp.capture_btn.click()
    wait_until(qapp, lambda: not ctrl.log_recording, timeout=4.0)
    win.state.refresh_devices()
    pump(qapp, 0.4)
    assert not ctrl.log_recording

    # 新 connection_id（重连）时再次自动触发，断线时自动停止并保存
    current_inv = DeviceInventory(devices=(dataclasses.replace(base_dev, connection_id="conn-auto-2"),), sources=())
    win.state.refresh_devices()
    wait_until(qapp, lambda: ctrl.log_recording and ctrl.log_serial == SERIAL, timeout=4.0)

    current_inv = DeviceInventory(devices=(), sources=())
    win.state.refresh_devices()
    wait_until(qapp, lambda: not ctrl.log_recording and ctrl.last_log is not None, timeout=4.0)
    assert Path(ctrl.last_log.path).is_file()

    # 恢复自动抓取开关与原设备列表
    if lp.sw_auto.isChecked():
        lp.sw_auto.click()
    monkeypatch.undo()
    win.state.refresh_devices()
    wait_until(qapp, lambda: any(d.serial == SERIAL for d in win.state.devices), timeout=4.0)


def test_simultaneous_dumps_use_distinct_paths_and_remove_failed_output(win, qapp, monkeypatch, tmp_path):
    from app.backend.api import CommandFailed
    from app.backend.models import LogcatCaptureResult
    from app.core import log_capture

    controller = win.state.log_capture
    barrier = threading.Barrier(2)
    saved, errors = [], []
    monkeypatch.setattr(win.state.settings, "export_dir", lambda: tmp_path)
    monkeypatch.setattr(log_capture, "time", SimpleNamespace(strftime=lambda _: "20261122_120000"))

    def dump(serial, buffers, path):
        if serial in ("A", "B"):
            barrier.wait(timeout=4)
        Path(path).write_text(serial, encoding="utf-8")
        if serial == "failed":
            raise CommandFailed("导出失败")
        return LogcatCaptureResult(path, tuple(buffers), len(serial))

    monkeypatch.setattr(win.state.backend, "dump_logcat", dump)
    for serial in ("A", "B"):
        controller.dump_log_now(serial, on_done=saved.append, on_error=errors.append)
    assert wait_until(qapp, lambda: len(saved) == 2 or errors)
    assert not errors
    paths = {Path(result.path) for result in saved}
    assert len(paths) == 2
    assert {path.read_text(encoding="utf-8") for path in paths} == {"A", "B"}

    controller.dump_log_now("failed", on_done=saved.append, on_error=errors.append)
    assert wait_until(qapp, lambda: len(errors) == 1)
    assert isinstance(errors[0], CommandFailed)
    assert set(tmp_path.joinpath("logcat").iterdir()) == paths


def test_auto_capture_waits_for_previous_connection_to_finish(win, qapp, monkeypatch):
    from app.backend.api import CommandFailed

    state = win.state
    controller = state.log_capture
    device = next(d for d in state.devices if d.serial == SERIAL)
    started, stop_requested = [], []
    original_devices = state.devices
    original_sources = state.inventory_sources

    def capture(serial, buffers, dest_path, on_progress, on_exit, *, clear_first=False):
        started.append((serial, on_exit))
        return SimpleNamespace(running=True, stop=lambda: stop_requested.append(serial))

    monkeypatch.setattr(state.backend, "start_logcat_capture", capture)
    state.select(SERIAL)
    state.settings.set("log_capture_auto", True)
    try:
        old = dataclasses.replace(device, connection_id="capture-a")
        new = dataclasses.replace(device, serial="new-device", connection_id="capture-b")
        state._set_devices([old])
        state._set_devices([new])
        assert state.current == new and stop_requested == [old.serial]
        assert [serial for serial, _ in started] == [old.serial]
        started[0][1](None, None)
        assert [serial for serial, _ in started] == [old.serial, new.serial]

        controller.stop_log_capture()
        started[1][1](None, CommandFailed("测试录制终止"))
        controller.check_auto_capture()
        assert len(started) == 2 and not controller.log_recording

        manual = dataclasses.replace(new, connection_id="capture-c")
        state.inventory_sources = (InventorySource("adb", "pending"),)
        state._set_devices([manual])
        assert controller.start_log_capture(manual.serial)
        state.inventory_sources = (InventorySource("adb", "ready"),)
        controller.stop_log_capture()
        started[2][1](None, None)
        assert len(started) == 3 and not controller.log_recording
    finally:
        state.settings.set("log_capture_auto", False)
        state.inventory_sources = original_sources
        if controller.log_recording:
            started[-1][1](None, None)
        state._set_devices(original_devices)
        state.select(SERIAL)


def test_worker_can_release_completion_callback_before_ui_dispatch(win, qapp, monkeypatch):
    from app.backend.models import LogcatCaptureResult

    lp = _open_log_capture(win, qapp)
    ctrl = win.state.log_capture

    def start_capture(serial, buffers, dest_path, on_progress, on_exit, *, clear_first=False):
        path = Path(dest_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"log")
        worker = threading.Thread(target=on_exit, args=(LogcatCaptureResult(dest_path, tuple(buffers), 3), None))
        worker.start()
        worker.join(2)
        assert not worker.is_alive()
        return SimpleNamespace(running=False, stop=lambda: None)

    monkeypatch.setattr(win.state.backend, "start_logcat_capture", start_capture)
    lp.capture_btn.click()
    assert wait_until(qapp, lambda: not ctrl.log_recording and ctrl.last_log is not None)
    assert ctrl.last_log.size_bytes == 3
