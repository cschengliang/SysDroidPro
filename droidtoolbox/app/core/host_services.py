"""本机服务：在电脑上启动 / 停止的辅助进程（与设备后端无关，演示模式下也可用）。

一个 HostService 由若干子进程组成；端口都能连上才算 running。输出逐行转发给界面。
目前只有 Winscope（Web 界面 + ADB 代理）；新增功能时继承 HostService，在 SERVICES 里登记。
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from tbui._qt import QtCore, Signal

from .settings import ROOT

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def python_exe() -> str:
    """子进程用 python.exe（pythonw 启动的界面里 sys.executable 是 pythonw.exe，没有标准输出）。"""
    exe = Path(sys.executable)
    cand = exe.with_name("python.exe")
    return str(cand if cand.exists() else exe)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.2) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@dataclass
class Proc:
    tag: str                         # 输出前缀，如 web / proxy
    args: list[str]
    port: int
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)


class HostService(QtCore.QObject):
    """状态：stopped → starting → running → stopping → stopped；启动失败为 error。"""

    stateChanged = Signal(str)
    output = Signal(str, str)        # (文本行, kind：output / error / system)
    _line = Signal(str, str)         # 读线程 → 主线程

    id = ""
    title = ""
    remark = ""
    start_timeout = 15.0

    def __init__(self):
        super().__init__()
        self.state = "stopped"
        self.error = ""
        self._procs: list[tuple[Proc, subprocess.Popen]] = []
        self._line.connect(self._on_line)
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._check)
        self._deadline = 0.0

    # 子类实现 --------------------------------------------------------
    def check_available(self) -> str | None:
        """返回不可用原因（中文），可用返回 None。"""
        return None

    def processes(self) -> list[Proc]:
        raise NotImplementedError

    def address(self) -> str:
        return ""

    def detail(self) -> str:
        """状态栏 / 表格里的补充信息，如端口。"""
        return ""

    def on_output(self, tag: str, line: str):
        """子类可解析输出（如读取令牌）。"""

    # 控制 ------------------------------------------------------------
    def _set(self, state: str, error: str = ""):
        self.state, self.error = state, error
        self.stateChanged.emit(state)

    def start(self):
        if self.state in ("starting", "running"):
            return
        reason = self.check_available()
        if reason:
            self._set("error", reason)
            self.output.emit(reason, "error")
            return
        procs = self.processes()
        busy = [p for p in procs if port_open(p.port)]
        if busy:
            msg = "端口已被占用：" + "、".join(f"{p.port}（{p.tag}）" for p in busy) + "。可能已有一个实例在运行，或在偏好设置中改端口。"
            self._set("error", msg)
            self.output.emit(msg, "error")
            return
        self._set("starting")
        for p in procs:
            env = {**os.environ, **p.env, "PYTHONUNBUFFERED": "1"}
            self.output.emit(" ".join(p.args), "input")   # 终端组件会自己加提示符
            try:
                proc = subprocess.Popen(p.args, cwd=p.cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                        stderr=subprocess.STDOUT, creationflags=NO_WINDOW)
            except OSError as e:
                self.output.emit(f"[{p.tag}] 启动失败：{e}", "error")
                self.stop()
                self._set("error", f"{p.tag} 启动失败：{e}")
                return
            self._procs.append((p, proc))
            threading.Thread(target=self._read, args=(p.tag, proc), daemon=True).start()
        self._deadline = time.time() + self.start_timeout
        self._timer.start()

    def stop(self):
        if not self._procs:
            if self.state != "error":
                self._set("stopped")
            return
        self._set("stopping")
        procs, self._procs = self._procs, []
        self._timer.stop()

        def kill_all():
            for _, proc in procs:
                if proc.poll() is None:
                    proc.terminate()
            for _, proc in procs:
                try:
                    proc.wait(3)
                except subprocess.TimeoutExpired:
                    proc.kill()
        kill_all()
        self.output.emit("已停止。", "system")
        self._set("stopped")

    def toggle(self):
        self.stop() if self.state in ("starting", "running") else self.start()

    # 内部 ------------------------------------------------------------
    def _read(self, tag: str, proc: subprocess.Popen):
        for raw in iter(proc.stdout.readline, b""):
            self._line.emit(tag, raw.decode("utf-8", "replace").rstrip("\r\n"))
        code = proc.wait()
        self._line.emit(tag, f"\x00exit {code}")

    def _on_line(self, tag: str, line: str):
        if line.startswith("\x00exit "):
            code = line[6:]
            if any(p.tag == tag for p, _ in self._procs):   # 主动 stop 后 _procs 已清空，不会走到这里
                # 运行中意外退出：整个服务视为失败，结束其余进程
                self.output.emit(f"[{tag}] 进程退出（{code}）", "error")
                self.stop()
                self._set("error", f"{tag} 意外退出（退出码 {code}）")
            return
        low = line.lower()
        kind = "error" if ("error" in low or "traceback" in low or "exception" in low) else "output"
        self.output.emit(f"[{tag}] {line}", kind)
        self.on_output(tag, line)

    def _check(self):
        if self.state != "starting":
            self._timer.stop()
            return
        if all(port_open(p.port) for p, _ in self._procs):
            self._timer.stop()
            self._set("running")
            self.output.emit(f"已就绪：{self.address()}", "system")
        elif time.time() > self._deadline:
            self.output.emit(f"启动超时（{self.start_timeout:.0f} 秒内端口未就绪）", "error")
            self.stop()
            self._set("error", "启动超时")


class WinscopeService(HostService):
    """Winscope：静态 Web 界面（http.server）+ ADB 代理（winscope_proxy.py，需要 adb 在 PATH 中）。"""

    id = "winscope"
    title = "Winscope"
    remark = "Android 17 Winscope Web 界面 + ADB 代理：在浏览器中抓取并查看 WindowManager / SurfaceFlinger / Transition 等 trace"
    TOKEN_FILE = Path.home() / ".config" / "winscope" / ".token"

    def __init__(self, settings, adb_dir_fn):
        super().__init__()
        self.settings = settings
        self._adb_dir_fn = adb_dir_fn          # () -> str | None：当前使用的 adb 所在目录
        self.token = ""

    @property
    def web_dir(self) -> Path:
        custom = self.settings.get("winscope_dir")
        return Path(custom) if custom else ROOT / "tool" / "tools" / "winscope-android17"

    @property
    def web_port(self) -> int:
        return int(self.settings.get("winscope_web_port"))

    @property
    def proxy_port(self) -> int:
        return int(self.settings.get("winscope_proxy_port"))

    def check_available(self):
        d = self.web_dir
        if not (d / "index.html").exists():
            return f"找不到 Winscope 网页文件：{d / 'index.html'}"
        if not (d / "winscope_proxy.py").exists():
            return f"找不到 ADB 代理脚本：{d / 'winscope_proxy.py'}"
        return None

    def processes(self):
        d = str(self.web_dir)
        env = {}
        adb_dir = self._adb_dir_fn()
        if adb_dir:
            env["PATH"] = adb_dir + os.pathsep + os.environ.get("PATH", "")
        py = python_exe()
        return [
            Proc("web", [py, "-m", "http.server", str(self.web_port), "--bind", "127.0.0.1", "--directory", d], self.web_port, d),
            # 经垫片启动：原脚本的 --port 没有 type=int，直接传端口会崩溃（见 winscope_proxy_shim.py）
            Proc("proxy", [py, str(Path(__file__).with_name("winscope_proxy_shim.py")), str(self.web_dir / "winscope_proxy.py"),
                           str(self.proxy_port)], self.proxy_port, d, env),
        ]

    def address(self) -> str:
        return f"http://127.0.0.1:{self.web_port}/"

    def detail(self) -> str:
        return f"web {self.web_port} · proxy {self.proxy_port}"

    def on_output(self, tag, line):
        if tag == "proxy" and line.startswith("Winscope token:"):
            self.token = line.split(":", 1)[1].strip()

    def read_token(self) -> str:
        if self.token:
            return self.token
        try:
            return self.TOKEN_FILE.read_text("utf-8").strip()
        except OSError:
            return ""
