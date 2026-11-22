"""应用级日志抓取与扩展录屏控制器：跨页面运行、支持连接 ADB 自动抓取、只保留最近一次成功记录。"""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from tbui._qt import QtCore, Signal

from ..backend.api import Cancelled
from ..backend.models import LogcatCaptureResult, ScreenrecordOptions, ScreenrecordResult

if TYPE_CHECKING:
    from .state import AppState

ALL_BUFFERS: tuple[str, ...] = ("main", "system", "crash", "events", "radio", "kernel")
DEFAULT_BUFFERS: tuple[str, ...] = ("main", "system", "crash")


@dataclass(frozen=True)
class CaptureRecord:
    """最近一次成功抓取 / Dump 的日志信息。"""
    path: str
    buffers: tuple[str, ...]
    size_bytes: int
    finished_at: str


@dataclass(frozen=True)
class VideoRecord:
    """最近一次成功录屏的信息。"""
    path: str
    size_bytes: int
    finished_at: str


def canonical_buffers(buffers: list[str] | tuple[str, ...]) -> list[str]:
    selected = set(buffers)
    return [b for b in ALL_BUFFERS if b in selected]


def parse_buffers(raw: str) -> list[str]:
    text = (raw or "").strip()
    if text == "none":
        return []
    tokens = {part.strip() for part in text.split(",") if part.strip()}
    parsed = [b for b in ALL_BUFFERS if b in tokens]
    return parsed if parsed else list(DEFAULT_BUFFERS)


def buffer_slug(buffers: list[str] | tuple[str, ...]) -> str:
    ordered = canonical_buffers(buffers)
    if not ordered:
        return "none"
    if len(ordered) == len(ALL_BUFFERS):
        return "all"
    return "-".join(ordered)


def buffer_summary(buffers: list[str] | tuple[str, ...]) -> str:
    ordered = canonical_buffers(buffers)
    if not ordered:
        return "none"
    if len(ordered) == len(ALL_BUFFERS):
        return f"all ({len(ALL_BUFFERS)})"
    return "+".join("sys" if b == "system" else b for b in ordered)


def _unique_path(directory: Path, stem: str, ext: str, *, reserve: bool = False) -> Path:
    if reserve:
        directory.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        suffix = f"-{index}" if index else ""
        candidate = directory / f"{stem}{suffix}{ext}"
        if reserve:
            try:
                candidate.touch(exist_ok=False)
            except FileExistsError:
                pass
            else:
                return candidate
        elif not candidate.exists():
            return candidate
        index += 1


def build_log_path(export_dir: Path, buffers: list[str] | tuple[str, ...],
                   prefix: str = "logcat", stamp: str | None = None, *, reserve: bool = False) -> Path:
    """生成按时间戳和缓冲区类型命名的本地日志路径（同秒冲突时自动加后缀）。"""
    log_dir = export_dir / "logcat"
    ts = stamp or time.strftime("%Y%m%d_%H%M%S")
    return _unique_path(log_dir, f"{prefix}_{ts}_{buffer_slug(buffers)}", ".log", reserve=reserve)


def build_video_path(export_dir: Path, stamp: str | None = None) -> Path:
    """生成按时间戳命名的本地录屏路径（同秒冲突时自动加后缀）。"""
    log_dir = export_dir / "logcat"
    ts = stamp or time.strftime("%Y%m%d_%H%M%S")
    return _unique_path(log_dir, f"screenrecord_{ts}", ".mp4")


class LogCaptureController(QtCore.QObject):
    """托管 logcat 落盘流、screenrecord 录屏流以及全局 ADB 自动抓取。"""

    stateChanged = Signal()
    videoFinished = Signal(bool)  # MP4 已保存

    def __init__(self, state: "AppState"):
        super().__init__(state)
        self.state = state
        self._auto_seen: set[tuple[str, str | None]] = set()

        # Logcat 抓取状态
        self._log_stream = None
        self._log_gen = 0
        self._log_callbacks = None
        self.log_recording = False
        self.log_stopping = False
        self._log_disconnected = False
        self.log_serial: str | None = None
        self.log_path = ""
        self.log_buffers: tuple[str, ...] = ()
        self.log_started_at = 0.0
        self.log_bytes = 0
        self.last_log: CaptureRecord | None = None

        # 录屏状态
        self._video_stream = None
        self._video_gen = 0
        self._linked_video_gen: int | None = None
        self._video_callback = None
        self.video_recording = False
        self.video_stopping = False
        self.video_serial: str | None = None
        self.video_path = ""
        self.video_started_at = 0.0
        self.last_video: VideoRecord | None = None

        state.devicesChanged.connect(self._on_devices)
        state.currentChanged.connect(self._on_current)
        state.rootChanged.connect(self._on_root_changed)
        state.settings.changed.connect(self._on_setting)
        state.jobs.add_cleanup(self.shutdown)

    @property
    def any_recording(self) -> bool:
        return self.log_recording or self.video_recording

    # 配置读写 --------------------------------------------------------
    def selected_buffers(self) -> list[str]:
        return parse_buffers(self.state.settings.get("log_capture_buffers"))

    def set_selected_buffers(self, buffers: list[str] | tuple[str, ...]):
        ordered = canonical_buffers(buffers)
        self.state.settings.set("log_capture_buffers", ",".join(ordered) if ordered else "none")
        self.stateChanged.emit()

    # 事件监听 --------------------------------------------------------
    def _on_setting(self, key: str):
        if key == "log_capture_auto" and self.state.settings.get("log_capture_auto"):
            # 开启开关时不立刻劫持当前已在线设备，仅对后续新接入/重连设备生效
            self._auto_seen = {(d.serial, d.connection_id) for d in self.state.devices if d.state == "device"}

    def _on_devices(self, devices):
        online = {(d.serial, d.connection_id) for d in devices if d.state == "device"}
        self._auto_seen &= online
        self._stop_if_disconnected(devices)
        self.check_auto_capture()

    def _on_current(self, _device):
        self._stop_if_disconnected(self.state.devices)
        self.check_auto_capture()

    def _on_root_changed(self, _serial: str):
        self.check_auto_capture()

    def _stop_if_disconnected(self, devices):
        adb_online = {d.serial for d in devices if d.is_adb}
        if self.log_recording and not self.log_stopping and self.log_serial not in adb_online:
            self._log_disconnected = True
            self.stop_log_capture()
        if self.video_recording and not self.video_stopping and self.video_serial not in adb_online:
            self.stop_screenrecord()

    def check_auto_capture(self):
        if self.log_recording or self.state.jobs.closed or not self.state.settings.get("log_capture_auto"):
            return
        stale = {s.name for s in self.state.inventory_sources if s.status in ("pending", "error")}
        if "adb" in stale:
            return
        cur = self.state.current
        if cur is None or cur.state != "device":
            return
        if cur.serial in self.state._auto_root_pending:
            return
        token = (cur.serial, cur.connection_id)
        if token in self._auto_seen:
            return
        self._auto_seen.add(token)
        self.start_log_capture(cur.serial, reason="ADB 自动抓取")

    # Logcat 抓取 -----------------------------------------------------
    def start_log_capture(self, serial: str, *, reason: str = "手动开始") -> bool:
        if self.log_recording or self.state.jobs.closed:
            return False
        buffers = self.selected_buffers()
        if not buffers:
            self.state.notify("warn", "请先选择缓冲区", "至少勾选一个日志缓冲区后再开始抓取。")
            return False
        try:
            dest = build_log_path(self.state.settings.export_dir(), buffers)
        except OSError as exc:
            self.state.report(exc, "无法创建日志导出目录")
            return False
        clear_first = bool(self.state.settings.get("log_capture_clear_before"))

        self._log_gen += 1
        gen = self._log_gen
        on_prog = self.state.jobs.on_ui(lambda n: gen == self._log_gen and self._on_log_progress(n))
        on_exit = self.state.jobs.on_ui(lambda res, err: gen == self._log_gen and self._on_log_exit(res, err))

        try:
            stream = self.state.jobs.own_stream(
                self.state.backend.start_logcat_capture(
                    serial, buffers, str(dest), on_prog, on_exit, clear_first=clear_first
                )
            )
        except BaseException as e:  # noqa: BLE001
            self.state.report(e, "无法开始抓取日志")
            return False

        # Keep the Qt receivers alive until their queued completion is delivered.
        self._log_callbacks = (on_prog, on_exit)
        self._log_stream = stream
        device = next((d for d in self.state.devices if d.serial == serial and d.state == "device"), None)
        if device is not None:
            self._auto_seen.add((serial, device.connection_id))
        self.log_recording = True
        self.log_stopping = False
        self._log_disconnected = False
        self.log_serial = serial
        self.log_path = str(dest)
        self.log_buffers = tuple(buffers)
        self.log_started_at = time.monotonic()
        self.log_bytes = 0
        self.stateChanged.emit()
        self.state.notify("ok", f"已开始抓取（{reason}）", str(dest))

        if self.state.settings.get("log_capture_sync_video") and self.start_screenrecord(serial, reason="随日志同步"):
            self._linked_video_gen = self._video_gen
        return True

    def stop_log_capture(self):
        if not self.log_recording or self.log_stopping:
            return
        self.log_stopping = True
        self.stateChanged.emit()
        if self._log_stream is not None:
            self._log_stream.stop()
        self._stop_linked_video()

    def _stop_linked_video(self):
        if self._linked_video_gen is not None and self._linked_video_gen == self._video_gen:
            self.stop_screenrecord()
        self._linked_video_gen = None

    def _on_log_progress(self, size_bytes: int):
        if self.log_recording:
            self.log_bytes = max(self.log_bytes, int(size_bytes))
            self.stateChanged.emit()

    def _on_log_exit(self, result: LogcatCaptureResult | None, err):
        self._stop_linked_video()
        self._log_callbacks = None
        self._log_stream = None
        self.log_recording = False
        self.log_stopping = False
        self.log_serial = None
        self.log_path = ""
        self.log_started_at = 0.0
        if err is not None and not isinstance(err, Cancelled):
            self.state.report(err, "日志抓取已中断")
        elif result is not None:
            self.log_bytes = result.size_bytes
            self.last_log = CaptureRecord(result.path, tuple(result.buffers), result.size_bytes, time.strftime("%H:%M:%S"))
            if result.interrupted or self._log_disconnected:
                self.state.notify("warn", "设备已断开，已保存部分日志", result.path)
            else:
                self.state.notify("ok", "日志抓取已保存", result.path)
        self.stateChanged.emit()
        self.check_auto_capture()

    def dump_log_now(self, serial: str, *, on_done=None, on_error=None):
        buffers = self.selected_buffers()
        if not buffers:
            self.state.notify("warn", "请先选择缓冲区", "至少勾选一个日志缓冲区后再导出缓存。")
            return None
        try:
            export_dir = self.state.settings.export_dir()
        except OSError as exc:
            if on_error:
                on_error(exc)
            else:
                self.state.report(exc, "无法创建日志导出目录")
            return None
        stamp = time.strftime("%Y%m%d_%H%M%S")

        def dump():
            dest = build_log_path(export_dir, buffers, prefix="logcat_dump", stamp=stamp, reserve=True)
            try:
                return self.state.backend.dump_logcat(serial, buffers, str(dest))
            except BaseException:
                dest.unlink(missing_ok=True)
                raise

        def done(res: LogcatCaptureResult):
            self.last_log = CaptureRecord(res.path, tuple(res.buffers), res.size_bytes, time.strftime("%H:%M:%S"))
            self.stateChanged.emit()
            self.state.notify("ok", "已导出缓冲区日志", res.path)
            if on_done:
                on_done(res)

        def fail(e):
            if on_error:
                on_error(e)
            else:
                self.state.report(e, "导出缓存失败")

        return self.state.jobs.run(dump, done=done, error=fail)

    # 屏幕录制（扩展功能）---------------------------------------------
    def start_screenrecord(self, serial: str, *, reason: str = "手动录屏") -> bool:
        if self.video_recording or self.state.jobs.closed:
            return False
        s = self.state.settings
        try:
            dest = build_video_path(s.export_dir())
        except OSError as exc:
            self.state.report(exc, "无法创建录屏导出目录")
            return False
        display_id = s.get("log_capture_display_id").strip() or None
        opts = ScreenrecordOptions(
            bugreport=bool(s.get("log_capture_bugreport")),
            bit_rate=int(s.get("log_capture_bitrate")),
            display_id=display_id,
        )

        self._video_gen += 1
        gen = self._video_gen
        on_exit = self.state.jobs.on_ui(lambda res, err: gen == self._video_gen and self._on_video_exit(res, err))

        try:
            stream = self.state.jobs.own_stream(
                self.state.backend.start_screenrecord(serial, str(dest), opts, on_exit)
            )
        except BaseException as e:  # noqa: BLE001
            self.state.report(e, "无法启动屏幕录制")
            return False

        self._video_callback = on_exit
        self._video_stream = stream
        self.video_recording = True
        self.video_stopping = False
        self.video_serial = serial
        self.video_path = str(dest)
        self.video_started_at = time.monotonic()
        self.stateChanged.emit()
        self.state.notify("ok", f"已开始录屏（{reason}）", dest.name)
        return True

    def stop_screenrecord(self):
        if not self.video_recording or self.video_stopping:
            return
        self.video_stopping = True
        self.stateChanged.emit()
        if self._video_stream is not None:
            self._video_stream.stop()

    def _on_video_exit(self, result: ScreenrecordResult | None, err):
        self._video_callback = None
        self._video_stream = None
        self.video_recording = False
        self.video_stopping = False
        self.video_serial = None
        self.video_path = ""
        self.video_started_at = 0.0
        if err is not None and not isinstance(err, Cancelled):
            self.state.report(err, "屏幕录制已中断")
        elif result is not None:
            self.last_video = VideoRecord(result.path, result.size_bytes, time.strftime("%H:%M:%S"))
            self.state.notify("ok", "录屏已保存", result.path)
        self.stateChanged.emit()
        self.videoFinished.emit(result is not None and err is None)

    # 清理 ------------------------------------------------------------
    def shutdown(self):
        self._log_gen += 1
        self._video_gen += 1
        self._log_callbacks = None
        self._video_callback = None
        if self._log_stream is not None:
            s, self._log_stream = self._log_stream, None
            s.stop()
        if self._video_stream is not None:
            v, self._video_stream = self._video_stream, None
            v.stop()
        self.log_recording = False
        self.log_stopping = False
        self.video_recording = False
        self.video_stopping = False
