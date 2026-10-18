"""命令流、持久 shell 和可降级的批量日志流。"""
import re
import threading
import time

from . import api
from ._base import detail, error_for, quote_args
from ._proc import ProcessStream


class ShellChannel(ProcessStream, api.ShellSession):
    def __init__(self, backend, serial, process, on_output, on_exit):
        self._backend, self._serial = backend, serial
        self._pid, self._prefix = None, ""
        self._pid_ready = threading.Event()

        def output(chunk, stream):
            if stream == "stdout" and not self._pid_ready.is_set():
                self._prefix += chunk
                match = re.search(r"(?m)^__DTB_PID__(\d+)\r?\n", self._prefix)
                if match:
                    self._pid = int(match[1])
                    self._pid_ready.set()
                    remaining = self._prefix[:match.start()] + self._prefix[match.end():]
                    self._prefix = ""
                    if remaining:
                        on_output(remaining, stream)
            else:
                on_output(chunk, stream)

        def finish(code, out, err, stopped, error):
            if stopped:
                on_exit(None)
            else:
                on_exit(error or error_for(code, out, err, serial))

        super().__init__(process, output, finish, health=lambda: backend._check_online(serial))
        self.send("echo __DTB_PID__$$")

    def send(self, command):
        with self._lock:
            if self._stop.is_set() or self.process.poll() is not None:
                raise api.CommandFailed("Shell 会话已结束，请重新连接")
            try:
                self.process.stdin.write((command + "\n").encode("utf-8"))
                self.process.stdin.flush()
            except OSError as exc:
                raise api.DeviceNotFound("Shell 连接已中断", str(exc)) from exc

    def interrupt(self):
        if not self._pid_ready.wait(1) or self._pid is None:
            raise api.CommandFailed("尚未取得 Shell 进程 ID，请稍后重试")
        # 只中断这条会话的子进程，不按程序名匹配其它会话。
        result = self._backend.shell(self._serial, quote_args("pkill", "-INT", "-P", self._pid))
        if result.exit_code not in (0, 1):
            raise api.CommandFailed("无法中断当前 Shell 命令", detail(result.stderr), result.exit_code)


class LogStream(api.StreamHandle):
    def __init__(self, backend, serial, buffers, tail, on_lines, on_exit):
        self._backend, self._serial, self._buffers, self._tail = backend, serial, list(buffers), tail
        self._lines, self._exit = on_lines, on_exit
        self._stop, self._lock = threading.Event(), threading.RLock()
        self._active = None
        self._pending, self._carry = [], ""
        self._last_flush = time.monotonic()
        self._thread = threading.Thread(target=self._run, daemon=True, name="dtb-logcat")
        self._thread.start()

    def _flush(self, force=False):
        with self._lock:
            if not self._stop.is_set() and self._pending and (force or len(self._pending) >= 500 or time.monotonic() - self._last_flush >= .075):
                while self._pending:
                    batch, self._pending = self._pending[:500], self._pending[500:]
                    self._lines(batch)
                    if self._stop.is_set():
                        break
                self._last_flush = time.monotonic()

    def _output(self, chunk, stream):
        if stream != "stdout":
            return
        with self._lock:
            if self._stop.is_set():
                return
            lines = (self._carry + chunk).split("\n")
            self._carry = lines.pop()
            self._pending.extend(line.rstrip("\r") for line in lines)
            self._flush()

    def _args(self):
        if self._buffers == ["dmesg"]:
            return ["shell", "dmesg -w"]
        args = ["logcat", "-v", "threadtime"]
        for buffer in self._buffers:
            args += ["-b", buffer]
        if self._tail is not None:
            args += ["-T", str(max(1, self._tail))]
        return args

    def _run(self):
        error = None
        try:
            while not self._stop.is_set():
                done, result = threading.Event(), []

                def finish(*args):
                    result.append(args)
                    done.set()

                with self._lock:
                    if self._stop.is_set():
                        break
                    process = self._backend._popen(self._backend._adb_args(self._serial, *self._args()))
                    self._active = ProcessStream(process, self._output, finish, tick=self._flush,
                                                 health=lambda: self._backend._check_online(self._serial))
                while not done.wait(.05):
                    if self._stop.is_set():
                        self._active.stop()
                code, out, err, stopped, failure = result[0]
                if stopped or self._stop.is_set():
                    break
                combined = (out + err).lower()
                if "kernel" in self._buffers and any(s in combined for s in ("invalid buffer", "unable to open log device", "unknown buffer", "permission denied")):
                    self._buffers = [b for b in self._buffers if b != "kernel"] or ["main", "system", "crash"]
                    self._pending.append("--------- toolbox: kernel 缓冲区不可用（需要 root），已忽略")
                    self._flush(True)
                    continue
                error = failure or error_for(code, out, err, self._serial)
                if error is None:
                    error = api.DeviceNotFound("日志连接已结束，请重新连接")
                break
        except api.BackendError as exc:
            error = exc
        except Exception as exc:
            error = api.CommandFailed("日志读取失败", str(exc))
        finally:
            if self._carry:
                self._pending.append(self._carry)
                self._carry = ""
            self._flush(True)
            self._exit(None if self._stop.is_set() else error)

    def stop(self):
        with self._lock:
            self._stop.set()
            active = self._active
        if active:
            active.stop()

    @property
    def running(self):
        return self._thread.is_alive() and not self._stop.is_set()


class StreamMethods:
    def _check_online(self, serial):
        result = self._adb(serial, "get-state", timeout=1)
        if result.stdout.strip() not in ("device", "recovery", "sideload"):
            raise api.DeviceNotFound(f"设备 {serial} 已断开")

    def start_command(self, serial, args, on_output, on_exit):
        process = self._popen(self._adb_args(serial, *args))

        def finish(code, out, err, stopped, error):
            if stopped:
                on_exit(None, None)
            else:
                error = error or error_for(code, out, err, serial, checked=False)
                on_exit(None if error else code, error)

        return ProcessStream(process, on_output, finish, health=lambda: self._check_online(serial))

    def open_shell(self, serial, on_output, on_exit):
        process = self._popen(self._adb_args(serial, "shell"))
        return ShellChannel(self, serial, process, on_output, on_exit)

    def start_logcat(self, serial, buffers, on_lines, on_exit, tail=2000):
        allowed = {"main", "system", "crash", "events", "radio", "kernel", "all", "dmesg"}
        if not buffers or any(b not in allowed for b in buffers) or ("dmesg" in buffers and buffers != ["dmesg"]):
            raise api.CommandFailed("请选择有效的日志缓冲区；dmesg 需要单独读取")
        return LogStream(self, serial, buffers, tail, on_lines, on_exit)

    def clear_logcat(self, serial, buffers):
        if buffers == ["dmesg"]:
            self._shell(serial, "dmesg -c")
        else:
            args = ["logcat", "-c"]
            for buffer in buffers:
                args += ["-b", buffer]
            self._adb(serial, *args)
