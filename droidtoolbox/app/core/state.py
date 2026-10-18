"""全局状态：设备列表轮询、当前设备、提示消息。页面通过 Context 访问后端与状态。"""
from __future__ import annotations

from tbui._qt import QtCore, Signal

from ..backend.api import Backend, BackendError, Cancelled
from ..backend.models import Device
from . import jobs
from .commands import CommandStore
from .settings import Settings


class AppState(QtCore.QObject):
    devicesChanged = Signal(list)          # list[Device]，任何字段变化都会发出
    currentChanged = Signal(object)        # Device | None：切换设备或当前设备状态（state）变化时发出
    notified = Signal(str, str, str)       # tone, title, body
    busyChanged = Signal(bool)             # 设备列表刷新中
    rootChanged = Signal(str)              # serial：root / remount 状态可能变化（自动 root 完成等），设备总览据此刷新

    def __init__(self, backend: Backend, settings: Settings, commands: CommandStore | None = None):
        super().__init__()
        self.backend = backend
        self.settings = settings
        self.commands = commands or CommandStore()
        self._auto_root_seen: set[str] = set()   # 本次上线已尝试过自动 root 的设备；离线后清除，重启回来会再次尝试
        self.devices: list[Device] = []
        self.current: Device | None = None
        self._serial: str | None = None
        self._polling = False
        self._poll_error_shown = False   # 同一个轮询错误只提示一次
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh_devices)
        settings.changed.connect(self._on_setting)

    def start(self):
        self.apply_tool_paths()
        self.refresh_devices()
        self._timer.start(self.settings.get("poll_interval_ms"))

    def apply_tool_paths(self):
        s = self.settings
        self.backend.configure(s.tool_path("adb_path"), s.tool_path("fastboot_path"), s.tool_path("scrcpy_path"))

    def _on_setting(self, key):
        if key == "poll_interval_ms":
            self._timer.setInterval(self.settings.get(key))
        elif key.endswith("_path"):
            self.apply_tool_paths()

    # 设备 ----------------------------------------------------------
    def refresh_devices(self):
        if self._polling:
            return
        self._polling = True
        self.busyChanged.emit(True)
        jobs.run(self.backend.list_devices, done=self._got_devices, error=self._poll_failed)

    def _poll_failed(self, e):
        self._polling = False
        self.busyChanged.emit(False)
        if not isinstance(e, Cancelled):
            self._set_devices([])
            if not self._poll_error_shown:
                self._poll_error_shown = True
                self.notify("danger", "无法获取设备列表", str(e))

    def _got_devices(self, devices: list[Device]):
        self._polling = False
        self._poll_error_shown = False
        self.busyChanged.emit(False)
        self._set_devices(devices)

    def _set_devices(self, devices: list[Device]):
        if devices != self.devices:
            self.devices = devices
            self.devicesChanged.emit(devices)
        self._auto_root(devices)
        cur = next((d for d in devices if d.serial == self._serial), None)
        if cur is None and devices and self._serial is None:
            cur = next((d for d in devices if d.is_adb), devices[0])
            self._serial = cur.serial
        prev = self.current
        self.current = cur
        if (prev is None) != (cur is None) or (prev and cur and (prev.serial != cur.serial or prev.state != cur.state)):
            self.currentChanged.emit(cur)

    def select(self, serial: str | None):
        if serial == self._serial:
            return
        self._serial = serial
        prev, self.current = self.current, next((d for d in self.devices if d.serial == serial), None)
        if prev != self.current:
            self.currentChanged.emit(self.current)

    @property
    def serial(self) -> str | None:
        return self.current.serial if self.current else None

    # 自动 root ------------------------------------------------------
    def _auto_root(self, devices: list[Device]):
        online = {d.serial for d in devices if d.state == "device"}
        self._auto_root_seen &= online
        if not self.settings.get("auto_root"):
            return
        for serial in sorted(online - self._auto_root_seen):
            self._auto_root_seen.add(serial)
            jobs.run(self._try_root, serial, done=lambda r, s=serial: self._auto_rooted(s, r),
                     error=lambda e, s=serial: self.notify("warn", "自动 root 失败", f"{s}：{e}"))

    def _try_root(self, serial: str) -> str:
        """在工作线程执行：user 固件跳过，已 root 不动。"""
        rs = self.backend.root_state(serial)
        if rs.build_type == "user":
            return "user"
        if rs.adbd_root:
            return "already"
        self.backend.root(serial, True)
        return "rooted"

    def _auto_rooted(self, serial: str, result: str):
        if result == "rooted":
            name = next((d.display_name for d in self.devices if d.serial == serial), serial)
            self.notify("ok", "已自动 adb root", f"{name}（可在偏好设置中关闭）")
        self.rootChanged.emit(serial)

    # 提示 ----------------------------------------------------------
    def notify(self, tone: str, title: str, body: str = ""):
        """tone = info / ok / warn / danger。"""
        self.notified.emit(tone, title, body)

    def report(self, e: BaseException, title: str | None = None):
        """把异常变成提示；取消不提示。"""
        if isinstance(e, Cancelled):
            return
        if isinstance(e, BackendError):
            detail = (e.detail or "").strip().splitlines()
            self.notify("danger", title or str(e), (str(e) if title else "") + ("\n" + detail[-1] if detail else ""))
        else:
            self.notify("danger", title or "发生了意外错误", f"{type(e).__name__}: {e}")


class Context:
    """页面拿到的依赖集合。"""

    def __init__(self, state: AppState, window):
        self.state = state
        self.backend = state.backend
        self.settings = state.settings
        self.commands = state.commands
        self.window = window

    def run(self, fn, *args, **kw) -> jobs.Job:
        return jobs.run(fn, *args, **kw)

    def notify(self, tone: str, title: str, body: str = ""):
        self.state.notify(tone, title, body)

    def report(self, e: BaseException, title: str | None = None):
        self.state.report(e, title)

    def open_page(self, page_id: str):
        self.window.open_page(page_id)
