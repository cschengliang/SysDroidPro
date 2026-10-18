import threading
import time

import pytest

from app.backend import api
from app.backend._base import BackendBase
from app.backend._streams import StreamMethods
from tests.fakes import FakeProcess, FakeRunner, fake_backend


class Streams(StreamMethods, BackendBase):
    pass


def wait_for(condition, timeout=2):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if condition():
            return True
        time.sleep(.01)
    return condition()


def test_command_nonzero_normal_exit_and_disconnect():
    for code, stderr, expected_code, typ in [(127, "not found", 127, type(None)), (1, "error: device offline", None, api.DeviceNotFound)]:
        runner, process = FakeRunner(), FakeProcess()
        runner.processes.append(process)
        backend = fake_backend(Streams, runner)
        results = []
        h = backend.start_command("s", ["shell", "false"], lambda *args: None, lambda *args: results.append(args))
        process.stderr.feed(stderr)
        process.finish(code)
        assert wait_for(lambda: results)
        assert results[0][0] == expected_code and isinstance(results[0][1], typ)
        h.stop()


def test_shell_hidden_pid_partial_output_and_interrupt():
    runner = FakeRunner.from_fixtures().add(("shell", "pkill -INT -P 1234"), (0, "", ""))
    backend = fake_backend(Streams, runner)
    output, done = [], threading.Event()
    session = backend.open_shell("s", lambda t, s: output.append(t), lambda e: done.set())
    session.send("echo hello")
    assert wait_for(lambda: "hello" in "".join(output))
    assert "__DTB_PID__" not in "".join(output)
    session.interrupt()
    assert runner.calls[-1][-1] == "pkill -INT -P 1234"
    session.stop()
    assert done.wait(2)
    with pytest.raises(api.CommandFailed):
        session.send("pwd")


def test_logcat_batching_partial_lines_and_stop():
    runner, process = FakeRunner(), FakeProcess()
    runner.processes.append(process)
    backend = fake_backend(Streams, runner)
    batches, results = [], []
    handle = backend.start_logcat("s", ["main", "system"], batches.append, results.append, tail=0)
    assert wait_for(lambda: runner.spawned)
    lines = [f"line {i}" for i in range(2000)]
    process.stdout.feed("\n".join(lines) + "\n")
    assert wait_for(lambda: sum(map(len, batches)) == 2000)
    assert [line for batch in batches for line in batch] == lines and len(batches) < 20
    assert all(len(batch) <= 500 for batch in batches)
    assert runner.calls[0][1:] == ["-s", "s", "logcat", "-v", "threadtime", "-b", "main", "-b", "system", "-T", "1"]
    handle.stop()
    count = len(batches)
    assert wait_for(lambda: results == [None])
    assert len(batches) == count and not handle.running


def test_kernel_fallback_only_drops_kernel():
    runner, first, second = FakeRunner(), FakeProcess(), FakeProcess()
    runner.processes.extend([first, second])
    backend = fake_backend(Streams, runner)
    batches, results = [], []
    handle = backend.start_logcat("s", ["main", "kernel"], batches.append, results.append)
    assert wait_for(lambda: len(runner.spawned) == 1)
    first.stderr.feed("Invalid buffer kernel")
    first.finish(1)
    assert wait_for(lambda: len(runner.spawned) == 2)
    assert "kernel" not in runner.calls[-1] and "main" in runner.calls[-1]
    second.stdout.feed("normal\n")
    assert wait_for(lambda: any("normal" in batch for batch in batches))
    assert batches[0][0].startswith("--------- toolbox:")
    handle.stop()


def test_dmesg_permission_and_log_disconnect():
    for buffers, output, error in [(["dmesg"], "dmesg: klogctl: Permission denied", api.PermissionDenied), (["main"], "error: device offline", api.DeviceNotFound)]:
        runner, process = FakeRunner(), FakeProcess()
        runner.processes.append(process)
        backend = fake_backend(Streams, runner)
        result = []
        handle = backend.start_logcat("s", buffers, lambda batch: None, result.append)
        assert wait_for(lambda: runner.spawned)
        process.stderr.feed(output)
        process.finish(1)
        assert wait_for(lambda: result) and isinstance(result[0], error)
        handle.stop()
