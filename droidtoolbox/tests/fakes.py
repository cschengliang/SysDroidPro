"""按命令匹配的 runner 与可控双管道进程；不调用真实工具。"""
from __future__ import annotations

from collections import deque
from pathlib import Path
import queue
import shlex
import subprocess
import threading
import time

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


class FakePipe:
    def __init__(self):
        self._queue = queue.Queue()
        self._rest = b""
        self.closed = False

    def feed(self, data):
        self._queue.put(data.encode("utf-8") if isinstance(data, str) else data)

    def read1(self, size):
        if not self._rest:
            self._rest = self._queue.get()
        data, self._rest = self._rest[:size], self._rest[size:]
        return data

    def close(self):
        if not self.closed:
            self.closed = True
            self.feed(b"")


class FakeInput:
    def __init__(self, callback=None):
        self.callback, self.data, self.closed = callback, [], False

    def write(self, data):
        if self.closed:
            raise BrokenPipeError()
        self.data.append(data)
        if self.callback:
            self.callback(data.decode("utf-8"))
        return len(data)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdout, self.stderr, self.stdin = FakePipe(), FakePipe(), FakeInput()
        self.returncode, self.terminated, self.killed = None, False, False
        self._done = threading.Event()

    def finish(self, code=0):
        if self.returncode is None:
            self.returncode = code
            self.stdout.close()
            self.stderr.close()
            self._done.set()

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.finish(-15)

    def kill(self):
        self.killed = True
        self.finish(-9)


class FakeRunner:
    def __init__(self, fixtures=False):
        self.fixtures = fixtures
        self.calls, self.spawned, self.environments = [], [], []
        self.rules, self.processes = [], deque()
        self._lock = threading.RLock()
        self.props = {}
        self.settings = {}
        self.root = False
        if fixtures:
            from app.backend.parsers import props
            self.props = props(fixture("getprop.emu36"))
            for ns in ("system", "secure", "global"):
                self.settings[ns, 0] = dict(line.split("=", 1) for line in fixture(f"settings_list_{ns}.emu36").splitlines() if "=" in line)

    @classmethod
    def from_fixtures(cls):
        return cls(fixtures=True)

    def add(self, matcher, result):
        """matcher 是尾部参数 tuple 或谓词；result 可为异常、回调或三元组。"""
        self.rules.append((matcher, result))
        return self

    def run(self, args, timeout, input=None):
        with self._lock:
            self.calls.append(list(args))
        for matcher, result in reversed(self.rules):
            matches = matcher(args) if callable(matcher) else tuple(args[-len(matcher):]) == tuple(matcher)
            if matches:
                if isinstance(result, Exception):
                    raise result
                if callable(result):
                    result = result(args)
                code, out, err = result
                return code, out.encode() if isinstance(out, str) else out, err.encode() if isinstance(err, str) else err
        if not self.fixtures:
            raise AssertionError(f"没有为命令配置 FakeRunner：{args}")
        return self._fixture_run(args)

    def _fixture_run(self, args):
        if "-s" in args:
            index = args.index("-s")
            serial, command = args[index + 1], args[index + 2:]
            if serial.startswith("no-such"):
                return 1, b"", f"error: device '{serial}' not found".encode()
        else:
            command = args[1:]
        if command in (["version"], ["--version"]):
            return 0, b"Version 37.0.1\nfastboot version 37.0.1\nscrcpy 4.1\n", b""
        if command == ["devices", "-l"]:
            return 0, (FIXTURES / "devices_l.emu36").read_bytes(), b""
        if command == ["devices"]:
            return 0, b"", b""
        if command == ["get-state"]:
            return 0, b"device\n", b""
        if command == ["root"] or command == ["unroot"]:
            self.root = command == ["root"]
            return 0, b"restarting adbd\n", b""
        if command == ["remount"]:
            return 0, b"Now reboot your device for settings to take effect\n", b""
        if command == ["wait-for-device"]:
            return 0, b"", b""
        if command[:2] == ["exec-out", "screencap"]:
            from app.backend.mock_data import fake_png
            return 0, fake_png(4, 4), b""
        if command[:1] != ["shell"]:
            return 1, b"", b"adb: unknown command\n"
        script = " ".join(command[1:])
        from app.backend._devices import DETAIL_SCRIPT, ROOT_SCRIPT
        if script == DETAIL_SCRIPT:
            return 0, (FIXTURES / "detail.emu36").read_bytes(), b""
        if script == ROOT_SCRIPT:
            return 0, (FIXTURES / ("root_state_root.emu36" if self.root else "root_state_nonroot.emu36")).read_bytes(), b""
        if script == "pidof dumpstate":
            return 1, b"", b""
        if script.startswith("getprop ro.product.manufacturer;"):
            return 0, b"Google\n16\n36\n==BATTERY\nlevel: 100\n", b""
        words = shlex.split(script)
        if words == ["getprop"]:
            return 0, "".join(f"[{k}]: [{v}]\n" for k, v in self.props.items()).encode(), b""
        if words == ["getprop", "-Z"]:
            return 0, (FIXTURES / "getprop_Z.emu36").read_bytes(), b""
        if words[:1] == ["setprop"]:
            if words[1].startswith("ro."):
                return 1, b"", b"Failed to set property\n"
            self.props[words[1]] = words[2]
            return 0, b"", b""
        if words[:1] == ["settings"]:
            user = 0
            if "--user" in words:
                index = words.index("--user")
                user = int(words[index + 1])
                words = words[:index] + words[index + 2:]
            _, action, ns, *rest = words
            values = self.settings.setdefault((ns, 0 if ns == "global" else user), {})
            if action == "list":
                return 0, "".join(f"{k}={v}\n" for k, v in values.items()).encode(), b""
            if action == "put":
                values[rest[0]] = rest[1]
            elif action == "delete":
                values.pop(rest[0], None)
            return 0, b"", b""
        mappings = {
            "service list": "service_list", "lshal --neat -itp": "lshal", "lshal -itp": "lshal_fallback",
            "ps -A -o PID,PPID,USER,RSS,S,NAME": "ps_A", "cmd user list -v": "cmd_user_list_v",
            "pm list users": "pm_list_users", "pm list packages --apex-only --show-versioncode -f": "apex_list",
            "pm list packages -f -U --show-versioncode --user 0": "pm_list_packages_user0",
            "pm list packages -s --user 0": "pm_list_packages_system", "pm list packages -d --user 0": "pm_list_packages_disabled",
            "dumpsys package packages": "dumpsys_package_packages", "ls -la /sdcard/": "ls_la_sdcard",
            "ls -la /": "ls_la_root", "dumpsys battery": "battery", "dumpsys dropbox": "dropbox", "date +%z": "date_offset",
        }
        if script in mappings:
            return 0, (FIXTURES / (mappings[script] + ".emu36")).read_bytes(), b""
        if words[:2] == ["dumpsys", "package"]:
            path = FIXTURES / (f"dumpsys_package_{words[2]}.emu36")
            return 0, path.read_bytes() if path.exists() else b"Unable to find package: no.such.package\n", b""
        if words[:3] == ["cmd", "package", "resolve-activity"]:
            return 0, (words[-1] + "/.MainActivity\n").encode(), b""
        if words[:3] == ["pm", "list", "packages"] and "-f" in words:
            lines = fixture("pm_list_packages_user0.emu36").splitlines()
            if words[-1] not in ("0", "-f"):
                lines = [line for line in lines if "=" + words[-1] + " " in line]
            return 0, ("\n".join(lines) + "\n").encode(), b""
        if "echo ==STATUS" in script:
            return 0, b"==STATUS\nName:\tinit\n==CMDLINE\ninit\n==THREADS\n1 init\n==LIBS\n==FD\n", b""
        if words[:1] == ["dumpsys"] or words[:2] == ["ps", "-o"]:
            return 0, b"", b""
        return 127, b"", b"/system/bin/sh: inaccessible or not found\n"

    def popen(self, args, *, env=None):
        self.calls.append(list(args))
        self.environments.append(env)
        process = self.processes.popleft() if self.processes else FakeProcess()
        self.spawned.append(process)
        if self.fixtures:
            if "logcat" in args:
                process.stdout.feed(fixture("logcat.emu36"))
            elif "bugreport" in args:
                def feed():
                    for i in range(20):
                        if process.poll() is not None:
                            return
                        process.stdout.feed(f"[{i}%] generating\r")
                        time.sleep(.05)
                threading.Thread(target=feed, daemon=True).start()
            elif args[-1] == "shell":
                def respond(command):
                    if "__DTB_PID__" in command:
                        process.stdout.feed("__DTB_PID__1234\n")
                    elif command.strip().startswith("echo "):
                        process.stdout.feed(command.strip()[5:] + "\n")
                process.stdin.callback = respond
        return process


def fake_backend(cls, runner=None):
    backend = cls(runner or FakeRunner.from_fixtures())
    backend._paths = {"adb": "adb", "fastboot": "fastboot", "scrcpy": "scrcpy"}
    return backend
