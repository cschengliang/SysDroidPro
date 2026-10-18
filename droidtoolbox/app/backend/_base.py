"""工具配置、统一错误映射和同步执行。设备级命令总是指定 serial。"""
from __future__ import annotations

import re
import shlex
import socket
import subprocess
import threading

from . import api
from ._proc import Runner
from ._tools import find_tool
from .models import ShellResult, ToolInfo


def text(data):
    return data.decode("utf-8", errors="replace") if isinstance(data, bytes) else data


def detail(output):
    return "\n".join(output.splitlines()[-20:])


def error_for(code, out, err, serial="", *, checked=True):
    raw = detail(out + "\n" + err).strip()
    low = raw.lower()
    if "unauthorized" in low:
        return api.DeviceUnauthorized("设备未授权：请在手机上允许 USB 调试", raw)
    if any(s in low for s in ("no devices/emulators found", "device offline", "device not found", "device disconnected",
                              "error: closed", "device still authorizing", "connection reset", "transport error")) or re.search(r"device .* not found", low):
        return api.DeviceNotFound(f"设备 {serial} 未连接或连接已中断", raw)
    if checked:
        if any(s in low for s in ("permission denied", "operation not permitted", "read-only file system", "must be root", "not running as root", "requires root", "securityexception")):
            return api.PermissionDenied("权限不足：请先取得 root 或检查设备权限", raw)
        if code:
            return api.CommandFailed("设备命令执行失败，请查看详细输出", raw, code)
    return None


def quote_args(*args):
    return " ".join(shlex.quote(str(arg)) for arg in args)


class BackendBase:
    def __init__(self, runner=None):
        self._runner = runner if runner is not None else Runner()
        self._config_lock = threading.RLock()
        self._paths = {}
        self._versions = {}
        self.configure(None, None, None)

    def configure(self, adb_path, fastboot_path, scrcpy_path):
        with self._config_lock:
            adb = find_tool("adb", adb_path)
            self._paths = {"adb": adb, "fastboot": find_tool("fastboot", fastboot_path, adb),
                           "scrcpy": find_tool("scrcpy", scrcpy_path)}
            self._versions.clear()

    def _tool(self, name):
        with self._config_lock:
            path = self._paths.get(name)
        if path is None:
            raise api.ToolNotFound(f"找不到 {name}：请在偏好设置中指定工具路径")
        return path

    def _run(self, args, timeout=30, input=None):
        try:
            return self._runner.run(args, timeout, input)
        except FileNotFoundError as exc:
            raise api.ToolNotFound("找不到命令工具：请检查偏好设置中的路径", str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise api.CommandTimeout(f"命令超时（{timeout:g} 秒）：{args[-1][:80]}", detail(text(exc.stderr or b""))) from exc
        except OSError as exc:
            raise api.CommandFailed("无法执行本机命令工具", str(exc)) from exc

    def _popen(self, args, *, env=None):
        try:
            return self._runner.popen(args, env=env) if env is not None else self._runner.popen(args)
        except FileNotFoundError as exc:
            raise api.ToolNotFound("找不到命令工具：请检查偏好设置中的路径", str(exc)) from exc
        except OSError as exc:
            raise api.CommandFailed("无法启动命令工具", str(exc)) from exc

    def _adb_args(self, serial, *args):
        return [self._tool("adb"), "-s", serial, *args]

    def _adb(self, serial, *args, timeout=30, checked=True):
        code, out, err = self._run(self._adb_args(serial, *args), timeout)
        out, err = text(out), text(err)
        error = error_for(code, out, err, serial, checked=checked)
        if error:
            raise error
        return ShellResult(out, err, code)

    def _shell(self, serial, script, timeout=30):
        result = self._adb(serial, "shell", script, timeout=timeout)
        return result.stdout

    def tool_info(self):
        versions = {}
        with self._config_lock:
            paths = dict(self._paths)
            for name, path in paths.items():
                if path and path not in self._versions:
                    try:
                        _, out, err = self._run([path, "version" if name == "adb" else "--version"], 3)
                        raw = text(out) + text(err)
                        pattern = {"adb": r"(?m)^Version ([\d.]+)", "fastboot": r"fastboot version (\S+)",
                                   "scrcpy": r"scrcpy ([\d.]+)"}[name]
                        match = re.search(pattern, raw)
                        self._versions[path] = match[1] if match else None
                    except api.BackendError:
                        self._versions[path] = None
                versions[name] = self._versions.get(path)
        try:
            with socket.create_connection(("127.0.0.1", 5037), timeout=0.3):
                running = True
        except OSError:
            running = False
        return ToolInfo(paths["adb"], versions["adb"], paths["fastboot"], versions["fastboot"],
                        paths["scrcpy"], versions["scrcpy"], running)

    def restart_server(self):
        for cmd in ("kill-server", "start-server"):
            code, out, err = self._run([self._tool("adb"), cmd])
            error = error_for(code, text(out), text(err))
            if error:
                raise error

    def shell(self, serial, command, timeout=30):
        return self._adb(serial, "shell", command, timeout=timeout, checked=False)

    def run_adb(self, serial, args, timeout=30):
        return self._adb(serial, *args, timeout=timeout, checked=False)
