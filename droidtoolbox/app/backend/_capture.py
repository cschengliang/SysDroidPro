"""真实设备的日志落盘与录屏。所有长任务共享应用的 ADB runner。"""
from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
from uuid import uuid4

from . import api, parsers
from ._base import error_for, quote_args, text
from ._proc import terminate
from .models import LogcatCaptureResult, ScreenrecordResult


_DEFAULT_BUFFERS = ("main", "system", "crash")
_CAPTURE_BUFFERS = (*_DEFAULT_BUFFERS, "events", "radio", "kernel")
_PID = re.compile(rb"(?m)^__DTB_RECORD_PID__([1-9][0-9]*)\r?$")


def _buffers(buffers):
    names = tuple(dict.fromkeys(_DEFAULT_BUFFERS if buffers is None else buffers))
    if not names or any(name not in _CAPTURE_BUFFERS for name in names):
        raise api.CommandFailed("请选择有效的 Logcat 缓冲区")
    return names


def _logcat_args(buffers, *options):
    args = ["logcat", *options]
    for name in buffers:
        args.extend(("-b", name))
    return args


def _kernel_denied(names, error):
    if "kernel" not in names or not isinstance(error, (api.PermissionDenied, api.CommandFailed)):
        return False
    message = (error.detail or "").lower()
    return "kernel" in message and any(word in message for word in (
        "permission", "not permitted", "unknown", "invalid", "unable", "failed", "unsupported",
    ))


def _without_kernel(names):
    effective = tuple(name for name in names if name != "kernel")
    if not effective:
        raise api.CommandFailed("设备不支持读取 kernel 日志缓冲区")
    return effective


@contextmanager
def _part_file(dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dest.parent, prefix=".dtb-", suffix=".part", delete=False) as tmp:
        path = Path(tmp.name)
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _tail(file, size=8192):
    end = file.seek(0, os.SEEK_END)
    file.seek(max(0, end - size))
    return file.read(size)


def _valid_mp4(path):
    """检查容器顶层结构，能识别常见的收尾失败与传输截断。"""
    end = path.stat().st_size
    boxes = set()
    offset = 0
    with path.open("rb") as stream:
        while offset < end:
            stream.seek(offset)
            header = stream.read(8)
            if len(header) != 8:
                return False
            size, kind = int.from_bytes(header[:4], "big"), header[4:]
            header_size = 8
            if size == 1:
                extended = stream.read(8)
                if len(extended) != 8:
                    return False
                size = int.from_bytes(extended, "big")
                header_size = 16
            elif size == 0:
                size = end - offset
            if size < header_size or offset + size > end:
                return False
            if size > header_size:
                boxes.add(kind)
            offset += size
    return {b"ftyp", b"mdat", b"moov"} <= boxes


class _CaptureTask(api.StreamHandle):
    def __init__(self, work, on_exit, release):
        self.stopped = threading.Event()
        self._lock = threading.RLock()
        self.thread = threading.Thread(target=self._run, args=(work, on_exit, release),
                                       daemon=True, name="dtb-capture")

    def _run(self, work, on_exit, release):
        result, error = None, None
        try:
            result = work(self)
        except api.BackendError as exc:
            error = exc
        except Exception as exc:
            error = api.CommandFailed("无法完成采集或保存文件", str(exc))
        try:
            on_exit(result, error)
        finally:
            release(self)

    def stop(self):
        with self._lock:
            self.stopped.set()

    def progress(self, callback, size):
        with self._lock:
            if not self.stopped.is_set():
                callback(size)

    @property
    def running(self):
        # 保存与远端清理期间仍需由 JobManager 持有。
        return self.thread.is_alive()


class CaptureMethods:
    def __init__(self, runner=None):
        self._capture_lock = threading.Lock()
        self._capture_closed = False
        self._captures = set()
        super().__init__(runner)

    def _start_capture(self, work, on_exit):
        with self._capture_lock:
            if self._capture_closed:
                raise api.Cancelled("已关闭")
            task = _CaptureTask(work, on_exit, self._release_capture)
            self._captures.add(task)
            task.thread.start()
            return task

    def _release_capture(self, task):
        with self._capture_lock:
            self._captures.discard(task)

    def close(self):
        with self._capture_lock:
            self._capture_closed = True
            tasks = tuple(self._captures)
        for task in tasks:
            task.stop()
        deadline = time.monotonic() + 15
        for task in tasks:
            if task.thread is not threading.current_thread():
                task.thread.join(max(0, deadline - time.monotonic()))
        # 让录屏有机会完成封装、拉取与清理，再关 runner。
        super().close()

    def _logcat_action(self, serial, names, *options):
        try:
            return self._adb(serial, *_logcat_args(names, *options), timeout=5), names
        except api.BackendError as error:
            if not _kernel_denied(names, error):
                raise
            names = _without_kernel(names)
            return self._adb(serial, *_logcat_args(names, *options), timeout=5), names

    def get_logcat_buffer_sizes(self, serial, buffers=None):
        result, names = self._logcat_action(serial, _buffers(buffers), "-g")
        parsed = {info.name: info for info in parsers.logcat_buffer_sizes(result.stdout)}
        if not parsed:
            raise api.CommandFailed("无法读取 Logcat 缓冲区容量", result.stderr or result.stdout)
        return [parsed[name] for name in names if name in parsed]

    def set_logcat_buffer_size(self, serial, size_mb, buffers=None):
        if type(size_mb) is not int or size_mb <= 0:
            raise api.CommandFailed("缓冲区大小必须是正整数")
        _, names = self._logcat_action(serial, _buffers(buffers), "-G", f"{size_mb}M")
        return self.get_logcat_buffer_sizes(serial, list(names))

    def _write_logcat(self, serial, names, path, *, task=None, on_progress=None):
        options = ("-v", "threadtime", "-d") if task is None else ("-v", "threadtime")
        with path.open("wb", buffering=0) as out, tempfile.TemporaryFile("w+b") as err:
            args = self._adb_args(serial, *_logcat_args(names, *options))
            with self._popen(args, stdout=out, stderr=err) as proc:
                deadline = time.monotonic() + 60 if task is None else None
                reported = 0
                stopped = False
                try:
                    while True:
                        try:
                            code = proc.wait(timeout=.2)
                            break
                        except subprocess.TimeoutExpired:
                            if task is not None:
                                if task.stopped.is_set():
                                    stopped = True
                                    break
                                size = path.stat().st_size
                                if size != reported:
                                    task.progress(on_progress, size)
                                    reported = size
                            elif time.monotonic() >= deadline:
                                raise api.CommandTimeout("导出 Logcat 超时")
                finally:
                    terminate(proc)
                error_text = text(_tail(err))
                if stopped:
                    # 本地进程由本会话主动终止。已有诊断错误仍需报告。
                    error = error_for(0, "", error_text, serial)
                    # ADB may write startup warnings to stderr even when it works.
                    if error is not None:
                        return error
                    failure = error_for(1, "", error_text, serial)
                    return failure if _kernel_denied(names, failure) else None
                error = error_for(code, "", error_text, serial)
                if task is not None and (error is None or "unexpected eof" in error_text.lower()):
                    if not task.stopped.is_set():
                        return api.DeviceNotFound("Logcat 连接已结束，请检查设备连接", error_text)
                return error

    def _save_log(self, serial, names, dest, *, task=None, on_progress=None, clear_first=False):
        try:
            with _part_file(dest) as path:
                if task is not None and task.stopped.is_set():
                    raise api.Cancelled("已取消")
                if clear_first:
                    # 先准备输出文件，再执行会删除设备缓存的命令。
                    _, names = self._logcat_action(serial, names, "-c")
                error = self._write_logcat(serial, names, path, task=task, on_progress=on_progress)
                if error and path.stat().st_size == 0 and _kernel_denied(names, error):
                    if task is not None and task.stopped.is_set():
                        raise api.Cancelled("已取消")
                    names = _without_kernel(names)
                    error = self._write_logcat(serial, names, path, task=task, on_progress=on_progress)
                size = path.stat().st_size
                interrupted = task is not None and size > 0 and isinstance(error, api.DeviceNotFound)
                if error and not interrupted:
                    raise error
                path.replace(dest)
                return LogcatCaptureResult(str(dest), names, size, interrupted=interrupted)
        except OSError as exc:
            raise api.CommandFailed("无法保存 Logcat 文件", str(exc)) from exc

    def dump_logcat(self, serial, buffers, dest_path):
        return self._save_log(serial, _buffers(buffers), Path(dest_path))

    def start_logcat_capture(self, serial, buffers, dest_path, on_progress, on_exit, *, clear_first=False):
        names = _buffers(buffers)
        self._tool("adb")
        return self._start_capture(lambda task: self._save_log(
            serial, names, Path(dest_path), task=task, on_progress=on_progress, clear_first=clear_first,
        ), on_exit)

    def list_displays(self, serial):
        result = self._adb(serial, "shell", "dumpsys SurfaceFlinger --display-id", timeout=5, checked=False)
        if result.exit_code:
            message = (result.stdout + result.stderr).lower()
            if any(word in message for word in ("unknown option", "unrecognized option", "invalid option", "not supported")):
                return []
            raise error_for(result.exit_code, result.stdout, result.stderr, serial)
        return parsers.physical_displays(result.stdout)

    def start_screenrecord(self, serial, dest_path, options, on_exit):
        display_id = (options.display_id or "").strip() or None
        if display_id and (re.fullmatch(r"[0-9]{1,20}", display_id) is None or int(display_id) >= 2**64):
            raise api.CommandFailed("Display ID 必须是有效的无符号 64 位整数")
        if type(options.bit_rate) is not int or options.bit_rate < 0:
            raise api.CommandFailed("录屏码率必须是非负整数")
        self._tool("adb")
        return self._start_capture(lambda task: self._save_screenrecord(
            task, serial, Path(dest_path), options, display_id,
        ), on_exit)

    def _record_to_device(self, task, serial, args, remote, diag):
        script = "echo __DTB_RECORD_PID__$$; exec " + quote_args(*args, remote)
        with diag.open("wb") as output, diag.open("rb") as reader:
            with self._popen(self._adb_args(serial, "shell", "-T", script),
                             stdout=output, stderr=subprocess.STDOUT) as proc:
                stop_deadline = None
                signalled = False
                try:
                    while True:
                        try:
                            code = proc.wait(timeout=.15)
                            break
                        except subprocess.TimeoutExpired:
                            if not task.stopped.is_set():
                                continue
                            if stop_deadline is None:
                                stop_deadline = time.monotonic() + 8
                            if time.monotonic() >= stop_deadline:
                                raise api.CommandTimeout("设备未及时结束录屏，无法确认 MP4 已保存")
                            if signalled:
                                continue
                            pid = _PID.search(_tail(reader))
                            if pid:
                                # 检查唯一临时路径，避免 PID 重用时误停其他进程。
                                pid = pid[1].decode("ascii")
                                check = quote_args("grep", "-Fxq", "--", remote)
                                signal = f"tr '\\000' '\\n' </proc/{pid}/cmdline | {check} && kill -2 {pid}"
                                result = self._adb(serial, "shell", signal, checked=False, timeout=2)
                                signalled = result.exit_code == 0
                finally:
                    terminate(proc)
                output_text = _PID.sub(b"", _tail(reader)).strip()
                # 一些 ROM 将主动 SIGINT 报告为 130，拉取后仍须验证文件结构。
                if not (signalled and code == 130):
                    error = error_for(code, "", text(output_text), serial)
                    if error:
                        raise error

    def _save_screenrecord(self, task, serial, dest, options, display_id):
        remote = f"/data/local/tmp/dtb_screenrecord_{uuid4().hex}.mp4"
        args = ["screenrecord"]
        if options.bugreport:
            args.append("--bugreport")
        if options.bit_rate:
            args.extend(("--bit-rate", str(options.bit_rate)))
        if display_id is not None:
            args.extend(("--display-id", display_id))
        with _part_file(dest) as path, _part_file(dest) as diag:
            if task.stopped.is_set():
                raise api.Cancelled("已取消")
            try:
                self._record_to_device(task, serial, args, remote, diag)
                self._adb(serial, "pull", remote, str(path), timeout=60)
                if not _valid_mp4(path):
                    raise api.CommandFailed("设备返回的录屏不完整，无法保存 MP4")
                size = path.stat().st_size
                path.replace(dest)
                return ScreenrecordResult(str(dest), size, display_id)
            finally:
                try:
                    self._adb(serial, "shell", quote_args("rm", "-f", remote), timeout=3)
                except api.BackendError:
                    pass
