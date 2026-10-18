import subprocess
import sys
import threading

from app.backend._proc import ProcessStream, Runner
from app.backend import _tools
from app.backend._base import BackendBase, error_for, quote_args
from app.backend import api
import pytest


def test_runner_binary_and_timeout():
    runner = Runner()
    code, out, err = runner.run([sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\x89PNG'); sys.stderr.write('err')"], 3)
    assert (code, out, err) == (0, b"\x89PNG", b"err")
    with pytest.raises(subprocess.TimeoutExpired):
        runner.run([sys.executable, "-c", "import time; time.sleep(2)"], 0.05)


def test_stream_partial_utf8_and_stop():
    done, chunks = threading.Event(), []
    p = Runner().popen([sys.executable, "-u", "-c", "import sys,time; sys.stdout.buffer.write(bytes([228,184])); sys.stdout.flush(); time.sleep(.1); sys.stdout.buffer.write(bytes([173])); sys.stdout.flush(); time.sleep(5)"])
    h = ProcessStream(p, lambda t, s: chunks.append(t), lambda *args: done.set())
    import time
    deadline = time.monotonic() + 2
    while "中" not in "".join(chunks) and time.monotonic() < deadline:
        time.sleep(.02)
    h.stop()
    count = len(chunks)
    assert done.wait(2) and len(chunks) == count
    assert "".join(chunks) == "中" and p.poll() is not None


@pytest.mark.parametrize("output,typ", [("error: device unauthorized", api.DeviceUnauthorized),
    ("error: device 's' not found", api.DeviceNotFound), ("error: device offline", api.DeviceNotFound),
    ("error: closed", api.DeviceNotFound), ("no devices/emulators found", api.DeviceNotFound),
    ("must be root", api.PermissionDenied), ("Permission denied", api.PermissionDenied),
    ("Operation not permitted", api.PermissionDenied), ("Read-only file system", api.PermissionDenied),
    ("other failure", api.CommandFailed)])
def test_errors(output, typ):
    error = error_for(1, "", output, "s")
    assert isinstance(error, typ) and error.detail == output


def test_quote_args():
    import shlex
    values = ["中文 a'b", "", "; echo injected", "a=b"]
    assert shlex.split(quote_args(*values)) == values


def test_tool_priority(tmp_path, monkeypatch):
    monkeypatch.setattr(_tools, "ROOT", tmp_path)
    name = "adb.exe" if sys.platform == "win32" else "adb"
    bundled = tmp_path / "tool/platform-tools" / name
    bundled.parent.mkdir(parents=True)
    bundled.touch()
    monkeypatch.setattr(_tools.shutil, "which", lambda n: "C:/path/adb.exe")
    assert _tools.find_tool("adb") == str(bundled.resolve())
    explicit = tmp_path / name
    explicit.touch()
    assert _tools.find_tool("adb", str(explicit)) == str(explicit.resolve())
    assert _tools.find_tool("adb", str(tmp_path / "missing")) is None


def test_base_maps_launch_errors():
    class Missing:
        def run(self, *args):
            raise FileNotFoundError("gone")
    b = BackendBase(Missing())
    with pytest.raises(api.ToolNotFound):
        b._run(["missing"])


def test_error_while_readers_have_pending_data_stops_threads():
    from tests.fakes import FakeProcess
    process, done, result = FakeProcess(), threading.Event(), []
    process.stdout.feed(b"x" * 2_000_000)
    def fail(*args):
        raise api.DeviceNotFound("设备已断开")
    handle = ProcessStream(process, fail, lambda *args: (result.append(args), done.set()))
    assert done.wait(2) and isinstance(result[0][-1], api.DeviceNotFound)
    assert not any(thread.is_alive() for thread in handle._readers)
    assert process.terminated


def test_sdk_and_path_discovery(tmp_path, monkeypatch):
    monkeypatch.setattr(_tools, "ROOT", tmp_path / "empty")
    monkeypatch.setattr(_tools.shutil, "which", lambda name: None)
    monkeypatch.delenv("ANDROID_SDK_ROOT", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setenv("ANDROID_HOME", str(tmp_path / "sdk"))
    tool = tmp_path / "sdk/platform-tools" / ("adb.exe" if sys.platform == "win32" else "adb")
    tool.parent.mkdir(parents=True)
    tool.touch()
    assert _tools.find_tool("adb") == str(tool.resolve())
    monkeypatch.setattr(_tools.shutil, "which", lambda name: str(tmp_path / "path"))
    assert _tools.find_tool("adb") == str((tmp_path / "path").resolve())


def test_successful_log_output_is_not_a_permission_error():
    assert error_for(0, "Permission denied in an old log entry", "") is None
