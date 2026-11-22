"""设备查询、root 状态与连接控制。"""
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import replace
import re
import threading
import time

from . import api, parsers as P
from ._base import detail, error_for, quote_args, text
from .models import DeviceInventory, InventorySource, RemountResult

DETAIL_SCRIPT = "\n".join(f"echo =={key}; {cmd}" for key, cmd in [
    ("PROPS", "getprop"), ("UNAME", "uname -r"), ("KERNEL", "cat /proc/version"),
    ("SELINUX", "getenforce"), ("MEM", "head -n 3 /proc/meminfo"), ("WM", "wm size; wm density"),
    ("UPTIME", "cat /proc/uptime"), ("BATTERY", "dumpsys battery"),
    ("CURRENT", "cat /sys/class/power_supply/battery/current_now 2>/dev/null || true"),
    ("DF", "df -k /data /system 2>/dev/null || true")])
ROOT_SCRIPT = "id -u; getprop ro.build.type; getprop ro.boot.veritymode; getprop partition.system.verified; mount | grep -E ' / | /system '"


class DeviceMethods:
    def configure(self, adb_path, fastboot_path, scrcpy_path):
        with self._inventory_lock:
            old_paths = tuple(getattr(self, "_paths", {}).get(k) for k in ("adb", "fastboot"))
            super().configure(adb_path, fastboot_path, scrcpy_path)
            if old_paths != tuple(self._paths[k] for k in ("adb", "fastboot")):
                self._inventory_epoch += 1
                self._device_cache.clear()
                self._device_sessions.clear()
                for f in (*self._pending.values(), *self._enumerations.values()):
                    f.cancel()
                self._pending.clear()
                self._enumerations.clear()
                self._snapshots.clear()

    def __init__(self, runner=None):
        self._inventory_lock = threading.RLock()
        self._pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="dtb-devices")
        self._closed = False
        self._inventory_epoch = 0
        self._device_cache = {}
        self._device_sessions = {}
        self._pending = {}
        self._enumerations = {}
        self._snapshots = {}
        super().__init__(runner)

    def close(self):
        with self._inventory_lock:
            self._closed = True
            self._inventory_epoch += 1
            self._pool.shutdown(wait=False, cancel_futures=True)
        super().close()

    def _enumerate(self, name, path, epoch):
        code, out, err = self._run([path, "devices", *(["-l"] if name == "adb" else [])], 0.6 if name == "adb" else 3)
        error = error_for(code, text(out), text(err))
        if error:
            raise error
        output = text(out)
        # A new transport can replace the same serial between two inventory polls.
        sessions = dict(re.findall(r"(?m)^(\S+)\s+\S+[^\r\n]*\btransport_id:(\d+)", output)) if name == "adb" else {}
        return tuple(replace(d, connection_id=f"{epoch}:{name}:{sessions.get(d.serial, '')}")
                     for d in P.devices(output, fastboot=name == "fastboot"))

    def _enrich(self, device):
        if device.is_fastboot:
            code, out, err = self._run([self._tool("fastboot"), "-s", device.serial, "getvar", "product"], 2)
            match = re.search(r"product:\s*(\S+)", text(out) + text(err))
            return replace(device, product=match[1] if code == 0 and match else None)
        result = self._shell(device.serial, "getprop ro.product.manufacturer; getprop ro.build.version.release; getprop ro.build.version.sdk; echo ==BATTERY; dumpsys battery", timeout=2)
        metadata, _, bat = result.partition("==BATTERY")
        fields = metadata.splitlines() + ["", "", ""]
        battery = P.battery(bat)
        return replace(device, manufacturer=fields[0] or None, android=fields[1] or None, api=P.integer(fields[2]),
                       battery=battery.level if battery else None)

    def list_devices(self):
        return list(self.poll_inventory().devices)

    def poll_inventory(self):
        names = ("adb", "fastboot")
        deadline = time.monotonic() + 0.85
        with self._inventory_lock:
            if self._closed:
                raise api.Cancelled("已关闭")
            epoch = self._inventory_epoch
            for name in names:
                if self._paths[name] and name not in self._enumerations:
                    self._enumerations[name] = self._pool.submit(self._enumerate, name, self._paths[name], epoch)
            futures = dict(self._enumerations)
        wait(futures.values(), timeout=0.55)
        with self._inventory_lock:
            if epoch != self._inventory_epoch:
                raise api.Cancelled("设备列表配置已变更")
            sources, merged = [], {}
            for name in names:
                future = futures.get(name)
                if future is None:
                    sources.append(InventorySource(name, "unavailable"))
                elif self._enumerations.get(name) is future and future.done():
                    self._enumerations.pop(name)
                    try:
                        self._snapshots[name] = future.result()
                    except api.BackendError as e:
                        sources.append(InventorySource(name, "error", str(e)))
                    else:
                        sources.append(InventorySource(name, "ready"))
                else:
                    sources.append(InventorySource(name, "pending"))

            # Prefer a fresh report over another source's retained snapshot.
            for source in sorted(sources, key=lambda s: s.status != "ready"):
                for d in self._snapshots.get(source.name, ()):
                    merged.setdefault(d.serial, d)

            sessions = {s: (d.state, d.connection_id) for s, d in merged.items()}
            for serial in set(sessions) | set(self._device_sessions):
                if sessions.get(serial) != self._device_sessions.get(serial):
                    self._invalidate_permission_cache(serial)
                    self._device_cache.pop(serial, None)
                    old = self._pending.pop(serial, None)
                    if old is not None:
                        old.cancel()
            self._device_sessions = sessions
            now = time.monotonic()
            for serial, dev in merged.items():
                if not (dev.is_adb or dev.is_fastboot):
                    continue
                cached = self._device_cache.get(serial)
                if not cached or now - cached[0] >= 30:
                    if serial not in self._pending:
                        self._pending[serial] = self._pool.submit(self._enrich, dev)
            pending = {s: f for s, f in self._pending.items() if s in merged}
        if pending:
            wait(pending.values(), timeout=max(0, deadline - time.monotonic()))
        with self._inventory_lock:
            if epoch != self._inventory_epoch:
                raise api.Cancelled("设备列表配置已变更")
            for serial, future in pending.items():
                if future.done() and self._pending.get(serial) is future:
                    self._pending.pop(serial)
                    try:
                        enriched = future.result()
                        if self._device_sessions.get(serial) == (enriched.state, enriched.connection_id):
                            self._device_cache[serial] = (time.monotonic(), enriched)
                    except api.BackendError:
                        pass
            devices = []
            for serial, dev in merged.items():
                if serial in self._device_cache:
                    cached = self._device_cache[serial][1]
                    dev = replace(dev, manufacturer=cached.manufacturer, android=cached.android, api=cached.api,
                                  battery=cached.battery, product=dev.product if dev.product is not None else cached.product)
                devices.append(dev)
            return DeviceInventory(tuple(devices), tuple(sources))

    def device_detail(self, serial):
        return P.device_detail(serial, self._shell(serial, DETAIL_SCRIPT))

    def list_apex_modules(self, serial):
        return P.apex(self._shell(serial, "pm list packages --apex-only --show-versioncode -f"))

    def root_state(self, serial):
        return P.root_state(self._shell(serial, ROOT_SCRIPT))

    def root(self, serial, enable=True):
        result = self._adb(serial, "root" if enable else "unroot", checked=False)
        self._invalidate_permission_cache(serial)
        output = result.stdout + result.stderr
        if "cannot run as root in production builds" in output:
            raise api.CommandFailed("user 版本固件不支持 adb root", detail(output))
        error = error_for(result.exit_code, result.stdout, result.stderr, serial)
        if error:
            raise error
        self._adb(serial, "wait-for-device", timeout=15)
        return output

    def remount(self, serial):
        result = self._adb(serial, "remount", checked=False)
        output = result.stdout + result.stderr
        error = error_for(result.exit_code or (1 if "not running as root" in output.lower() else 0), result.stdout, result.stderr, serial)
        if isinstance(error, api.PermissionDenied):
            raise error
        if result.exit_code or re.search(r"remount failed|not.*writable|failed to remount", output, re.I):
            raise api.CommandFailed("重新挂载失败：模拟器可能未以 -writable-system 启动，或需要先处理 verity 并重启", detail(output), result.exit_code)
        needs = "reboot" in output.lower() and any(s in output.lower() for s in ("take effect", "now reboot", "reboot your device", "reboot the device"))
        return RemountResult(output, needs)

    def disable_verity(self, serial):
        result = self._adb(serial, "disable-verity")
        return result.stdout + result.stderr

    def connect(self, address):
        if ":" not in address:
            address += ":5555"
        result = self._adb(address, "connect", address)
        match = re.search(r"(?:already )?connected to (\S+)", result.stdout + result.stderr)
        if not match:
            raise api.CommandFailed(f"无法连接到 {address}：请检查地址与无线调试状态", detail(result.stdout + result.stderr))
        self._invalidate_permission_cache(match[1])
        return match[1]

    def pair(self, address, code):
        result = self._adb(address, "pair", address, code)
        if "Successfully paired" not in result.stdout + result.stderr:
            raise api.CommandFailed("配对失败：请检查配对码与无线调试状态", detail(result.stdout + result.stderr))

    def disconnect(self, serial):
        self._adb(serial, "disconnect", serial)
        self._invalidate_permission_cache(serial)

    def reboot(self, serial, mode="system"):
        if mode not in ("system", "recovery", "bootloader", "fastboot", "sideload", "poweroff"):
            raise api.CommandFailed("不支持的重启方式")
        device = next((dev for dev in self.list_devices() if dev.serial == serial), None)
        if device is None:
            raise api.DeviceNotFound(f"设备 {serial} 未连接")
        if device.is_fastboot:
            if mode in ("sideload", "poweroff"):
                raise api.CommandFailed("Fastboot 模式下不支持该重启方式")
            code, out, err = self._run([self._tool("fastboot"), "-s", serial, "reboot", *([] if mode == "system" else [mode])])
            error = error_for(code, text(out), text(err), serial)
            if error:
                raise error
        elif mode == "poweroff":
            self._shell(serial, "reboot -p")
        else:
            self._adb(serial, "reboot", *([] if mode == "system" else [mode]))

    def send_key(self, serial, keycode):
        self._shell(serial, quote_args("input", "keyevent", keycode))

    def screenshot(self, serial):
        code, out, err = self._run(self._adb_args(serial, "exec-out", "screencap", "-p"))
        error = error_for(code, "", text(err), serial)
        if error:
            raise error
        if not out.startswith(b"\x89PNG\r\n\x1a\n"):
            raise api.CommandFailed("截屏失败：设备没有返回有效的 PNG 图像", text(err))
        return out
