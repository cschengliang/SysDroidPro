"""全局状态：设备列表轮询、当前设备、提示消息。页面通过 Context 访问后端与状态。"""
from __future__ import annotations

from tbui._qt import QtCore, Signal

from ..backend.api import Backend, BackendError, Cancelled
from ..backend.models import Device, DeviceInventory
from . import jobs
from .commands import CommandStore
from .settings import Settings


class AppState(QtCore.QObject):
    devicesChanged = Signal(list)          # list[Device]，任何字段变化都会发出
    currentChanged = Signal(object)        # Device | None：选择或连接代次变化
    inventoryChanged = Signal(object)      # tuple[InventorySource, ...]
    notified = Signal(str, str, str)       # tone, title, body
    busyChanged = Signal(bool)             # 设备列表刷新中
    rootChanged = Signal(str)              # serial：root / remount 状态可能变化（自动 root 完成等），设备总览据此刷新

    def __init__(self, backend: Backend, settings: Settings, commands: CommandStore | None = None):
        super().__init__()
        self.backend = backend
        self.jobs = jobs.JobManager(self.report)
        self.jobs.add_cleanup(backend.close)
        self.settings = settings
        self.commands = commands or CommandStore()
        self._auto_root_seen = set()    # 每个在线 transport 至多尝试一次
        self.devices: list[Device] = []
        self.current: Device | None = None
        self.generation = 0
        self.inventory_sources = ()
        self._serial: str | None = None
        self._polling = False
        self._poll_again = False
        self._poll_epoch = 0
        self._poll_error_shown = False   # 同一个轮询错误只提示一次
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self.refresh_devices)
        settings.changed.connect(self._on_setting)

    def start(self):
        if self.jobs.closed:
            return
        self.apply_tool_paths()
        self.refresh_devices()
        self._timer.start(self.settings.get("poll_interval_ms"))

    def shutdown(self):
        self._timer.stop()
        self.jobs.close()

    def apply_tool_paths(self):
        s = self.settings
        self.backend.configure(s.tool_path("adb_path"), s.tool_path("fastboot_path"), s.tool_path("scrcpy_path"))

    def _on_setting(self, key):
        if self.jobs.closed:
            return
        if key == "poll_interval_ms":
            self._timer.setInterval(self.settings.get(key))
        elif key in ("adb_path", "fastboot_path", "scrcpy_path"):
            self.apply_tool_paths()
            if key != "scrcpy_path":
                self._poll_epoch += 1
                if self._polling:
                    self._poll_again = True
                elif self._timer.isActive():
                    self.refresh_devices()

    # 设备 ----------------------------------------------------------
    def refresh_devices(self):
        if self._polling or self.jobs.closed:
            return
        self._polling = True
        epoch = self._poll_epoch
        self.busyChanged.emit(True)
        self.jobs.run(self.backend.poll_inventory,
                      done=lambda result: self._poll_finished(epoch, result=result),
                      error=lambda e: self._poll_finished(epoch, error=e))

    def _poll_finished(self, epoch, *, result: DeviceInventory | None = None, error=None):
        self._polling = False
        self.busyChanged.emit(False)
        if epoch == self._poll_epoch:
            if result is not None:
                self._poll_error_shown = False
                if result.sources != self.inventory_sources:
                    self.inventory_sources = result.sources
                    self.inventoryChanged.emit(result.sources)
                self._set_devices(list(result.devices))
            elif not isinstance(error, Cancelled) and not self._poll_error_shown:
                self._poll_error_shown = True
                self.notify("danger", "无法获取设备列表", str(error))
        if self._poll_again:
            self._poll_again = False
            self.refresh_devices()

    def _set_devices(self, devices: list[Device]):
        appeared = {d.serial for d in devices} - {d.serial for d in self.devices}
        changed = devices != self.devices
        self.devices = devices
        cur = next((d for d in devices if d.serial == self._serial), None)
        if cur is None and devices:
            # 从未选过：选第一台；选中的设备已不在列表：只在有新设备插入时切换过去
            # （已选设备重启时会短暂消失，期间不因为别的设备一直在线就切走，回来后仍选中它）
            pool = devices if self._serial is None else [d for d in devices if d.serial in appeared]
            if pool:
                cur = next((d for d in pool if d.is_adb), pool[0])
                self._serial = cur.serial
        current_changed = self._update_current(cur)
        if changed:
            self.devicesChanged.emit(devices)
        if current_changed:
            self.currentChanged.emit(self.current)
        self._auto_root(devices)

    def select(self, serial: str | None):
        if serial == self._serial:
            return
        self._serial = serial
        if self._update_current(next((d for d in self.devices if d.serial == serial), None)):
            self.currentChanged.emit(self.current)

    def _update_current(self, device):
        def key(d):
            return (d.serial, d.state, d.connection_id) if d else None

        changed = key(device) != key(self.current)
        self.current = device
        if changed:
            self.generation += 1
        return changed

    @property
    def serial(self) -> str | None:
        return self.current.serial if self.current else None

    # 自动 root ------------------------------------------------------
    def _auto_root(self, devices: list[Device]):
        stale = {s.name for s in self.inventory_sources if s.status in ("pending", "error")}
        online = {(d.serial, d.connection_id) for d in devices if d.state == "device"}
        self._auto_root_seen &= online
        if "adb" in stale or not self.settings.get("auto_root"):
            return
        for serial, connection in sorted(online - self._auto_root_seen):
            self._auto_root_seen.add((serial, connection))
            self.jobs.run(self._try_root, serial, done=lambda r, s=serial: self._auto_rooted(s, r),
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
        return self.state.jobs.run(fn, *args, **kw)

    def start_stream(self, factory, *args, **kw):
        if self.state.jobs.closed:
            raise Cancelled("已关闭")
        return self.state.jobs.own_stream(factory(*args, **kw))

    def on_ui(self, callback):
        return self.state.jobs.on_ui(callback)

    def notify(self, tone: str, title: str, body: str = ""):
        self.state.notify(tone, title, body)

    def report(self, e: BaseException, title: str | None = None):
        self.state.report(e, title)

    def open_page(self, page_id: str):
        self.window.open_page(page_id)
