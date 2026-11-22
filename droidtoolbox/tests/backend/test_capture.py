"""回归覆盖真实采集后端的落盘、降级和录屏收尾。"""
import subprocess
import threading
from pathlib import Path

import pytest

from app.backend import api, parsers
from app.backend.adb import AdbBackend
from app.backend.models import DisplayInfo, LogcatBufferInfo, ScreenrecordOptions


class _Exited:
    def __init__(self, code):
        self.code = code

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def poll(self):
        return self.code

    def wait(self, timeout=None):
        return self.code


class _Recording(_Exited):
    def __init__(self):
        self.done = threading.Event()
        super().__init__(None)

    def poll(self):
        return 130 if self.done.is_set() else None

    def wait(self, timeout=None):
        if self.done.wait(timeout):
            return 130
        raise subprocess.TimeoutExpired("screenrecord", timeout)

    def terminate(self):
        self.done.set()


class CaptureRunner:
    def __init__(self):
        self.calls = []
        self.log_replies = []
        self.recording = _Recording()
        self.record_started = threading.Event()
        self.remote = None
        self.mp4 = b""
        self.on_close = None

    def popen(self, args, *, stdout, stderr):
        command = tuple(args[3:])
        self.calls.append(command)
        if command[0] == "logcat":
            code, output, error = self.log_replies.pop(0)
            stdout.write(output)
            stderr.write(error)
            return _Exited(code)
        assert command[:2] == ("shell", "-T")
        self.remote = command[2].split()[-1]
        stdout.write(b"__DTB_RECORD_PID__123\n")
        stdout.flush()
        self.record_started.set()
        return self.recording

    def run(self, args, timeout, input=None):
        command = tuple(args[3:])
        self.calls.append(command)
        if command[0] == "logcat":
            if "-g" in command:
                return 0, b"main: ring buffer is 64 MiB (10 Kb consumed)\n", b""
            return 0, b"", b""
        if command[0] == "pull":
            assert command[1] == self.remote and self.recording.poll() == 130
            Path(command[2]).write_bytes(self.mp4)
            return 0, b"", b""
        assert command[0] == "shell"
        if command[1].startswith("tr "):
            assert self.remote in command[1] and command[1].endswith("kill -2 123")
            self.recording.done.set()
        else:
            assert command[1] == f"rm -f {self.remote}"
        return 0, b"", b""

    def close(self):
        if self.on_close:
            self.on_close()


@pytest.fixture
def capture():
    runner = CaptureRunner()
    backend = AdbBackend(runner)
    with backend._config_lock:
        backend._paths["adb"] = "fake-adb"
    try:
        yield backend, runner
    finally:
        backend.close()


def test_parse_sizes_and_physical_displays():
    assert parsers.logcat_buffer_sizes(
        "main: ring buffer is 16 MiB (12.3 MiB consumed), max entry is 5120 B\n"
        "system: ring buffer is 256 Kb (10 Kb consumed)\n"
        "crash: ring buffer is 32 MB\n"
        "events: ring buffer is 1.5 GiB (0 B consumed, 0 B readable)\n"
    ) == [
        LogcatBufferInfo("main", 16 * 1024**2, 12_897_484),
        LogcatBufferInfo("system", 256 * 1024, 10 * 1024),
        LogcatBufferInfo("crash", 32 * 1024**2),
        LogcatBufferInfo("events", 3 * 1024**3 // 2, 0),
    ]
    assert parsers.physical_displays(
        'Display 18446744073709551615 (HWC display 0): displayName="Internal"\n'
        "Display 18446744073709551615 (HWC display 0):\n"
        "Display 42 (HWC display 1): displayName=\"\"\n"
        "mDisplayId=2\n"
    ) == [DisplayInfo("18446744073709551615", "Internal"), DisplayInfo("42", "HWC display 1")]


def test_buffer_resize_reports_device_value(capture):
    backend, runner = capture
    assert backend.set_logcat_buffer_size("device", 128, ["main"]) == [
        LogcatBufferInfo("main", 64 * 1024**2, 10 * 1024),
    ]
    assert runner.calls == [("logcat", "-G", "128M", "-b", "main"), ("logcat", "-g", "-b", "main")]


def test_dump_retries_kernel_and_preserves_raw_bytes(capture, tmp_path):
    backend, runner = capture
    runner.log_replies = [
        (1, b"", b"logcat: Unable to open log device 'kernel': Permission denied"),
        (0, b"\xfflog\n", b""),
    ]
    dest = tmp_path / "dump.log"
    result = backend.dump_logcat("device", ["main", "kernel", "main"], str(dest))
    assert (result.buffers, result.size_bytes) == (("main",), 5)
    assert dest.read_bytes() == b"\xfflog\n"
    assert list(tmp_path.iterdir()) == [dest]


def test_dump_failure_keeps_existing_file(capture, tmp_path):
    backend, runner = capture
    runner.log_replies = [(1, b"partial", b"adb: device offline")]
    dest = tmp_path / "dump.log"
    dest.write_bytes(b"existing")
    with pytest.raises(api.DeviceNotFound):
        backend.dump_logcat("device", ["main", "kernel"], str(dest))
    assert dest.read_bytes() == b"existing"
    assert list(tmp_path.iterdir()) == [dest]
    assert len(runner.calls) == 1


def test_capture_disconnect_saves_partial_and_notifies_once(capture, tmp_path):
    backend, runner = capture
    runner.log_replies = [(1, b"some logs", b"adb: device offline")]
    dest = tmp_path / "capture.log"
    outcomes = []
    handle = backend.start_logcat_capture("device", ["main"], str(dest), lambda _: None,
                                          lambda *outcome: outcomes.append(outcome))
    handle.thread.join(3)
    assert len(outcomes) == 1
    result, error = outcomes[0]
    assert error is None and result.interrupted and result.size_bytes == 9
    assert dest.read_bytes() == b"some logs"
    assert all("-c" not in cmd for cmd in runner.calls)


def _box(kind):
    return b"\0\0\0\x09" + kind + b"x"


@pytest.mark.parametrize("complete", [True, False])
def test_screenrecord_close_stops_then_pulls_and_cleans(capture, tmp_path, complete):
    backend, runner = capture
    runner.mp4 = _box(b"ftyp") + _box(b"mdat") + (_box(b"moov") if complete else b"")
    dest = tmp_path / "record.mp4"
    outcomes = []
    closed_after = []
    runner.on_close = lambda: closed_after.append(bool(outcomes))
    handle = backend.start_screenrecord("device", str(dest), ScreenrecordOptions(),
                                        lambda *outcome: outcomes.append(outcome))
    assert runner.record_started.wait(3)
    handle.stop()
    handle.stop()
    backend.close()
    assert len(outcomes) == 1 and closed_after == [True]
    result, error = outcomes[0]
    if complete:
        assert error is None and result.size_bytes == len(runner.mp4)
        assert dest.read_bytes() == runner.mp4
        assert list(tmp_path.iterdir()) == [dest]
    else:
        assert result is None and isinstance(error, api.CommandFailed)
        assert not list(tmp_path.iterdir())
    assert runner.calls[-1] == ("shell", f"rm -f {runner.remote}")
