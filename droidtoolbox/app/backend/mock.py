"""演示后端：不连接设备，返回固定或随机数据，供界面开发与截图使用。

模拟延迟让加载态、进度条都能被看到；行为尽量贴近 userdebug 设备上的真实 adb：
- adbd 默认非 root，adb root 后才能 remount、启停 init 服务、debuggerd、启停组件；
- 第一次 remount 会关闭 verity 并要求重启；重启后 adbd 回到非 root、/system 回到只读，需要再 root + remount。
样例数据在 mock_data.py。
"""
from __future__ import annotations

import random
import threading
import time

from . import api
from . import mock_data as D
from .api import Backend, BackendError, Cancelled, CommandFailed, DeviceNotFound, PermissionDenied, StreamHandle
from .models import (AndroidUser, ApexModule, BatteryInfo, BinderService, Component, Device, DeviceDetail,
                     FileEntry, HalInfo, InitService, PackageDetail, PackageInfo, Permission, ProcessDetail,
                     ProcessInfo, Progress, RemountResult, RootState, ShellResult, StorageInfo, ToolInfo)

GB, MB = D.GB, D.MB


def _sleep(a=0.08, b=0.25):
    time.sleep(random.uniform(a, b))


class _Thread(StreamHandle):
    """用线程模拟一条流。target(self) 在循环中检查 self.stopped。"""

    def __init__(self, target, on_exit=None):
        self._stop = threading.Event()
        self._emit_lock = threading.Lock()
        self._on_exit = on_exit
        self._target = target
        self._t = threading.Thread(target=self._run, daemon=True)
        self._t.start()

    def _run(self):
        err = None
        try:
            self._target(self)
        except BackendError as e:
            err = e
        if self._on_exit:
            self._on_exit(err)

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def wait(self, s: float) -> bool:
        return self._stop.wait(s)

    def emit(self, fn, *args):
        """stop() 返回后保证不再回调：两者共用一把锁。"""
        with self._emit_lock:
            if not self._stop.is_set():
                fn(*args)

    def stop(self):
        with self._emit_lock:
            self._stop.set()

    @property
    def running(self) -> bool:
        return self._t.is_alive() and not self._stop.is_set()


class _MockShell(api.ShellSession):
    def __init__(self, backend: "MockBackend", serial: str, on_output, on_exit):
        self._b, self._serial, self._out, self._exit = backend, serial, on_output, on_exit
        self._cwd = "/"
        self._root = backend._st(serial)["root"]
        self._alive = True

    def send(self, command: str):
        if self._alive:
            threading.Thread(target=self._run, args=(command.strip(),), daemon=True).start()

    def _run(self, cmd):
        _sleep(0.03, 0.12)
        if cmd in ("exit", "logout"):
            if self._root and not self._b._st(self._serial)["root"]:
                self._root = False
                return
            self.stop()
            return
        if cmd == "su":
            self._root = True
            return
        if cmd.startswith("cd"):
            target = cmd[2:].strip() or "/"
            self._cwd = target if target.startswith("/") else (self._cwd.rstrip("/") + "/" + target)
            return
        if cmd == "pwd":
            self._out(self._cwd + "\n", "stdout")
            return
        if cmd in ("id", "whoami"):
            self._out(("uid=0(root) gid=0(root) context=u:r:su:s0" if self._root else
                       "uid=2000(shell) gid=2000(shell) context=u:r:shell:s0") + "\n", "stdout")
            return
        r = self._b.shell(self._serial, cmd)
        if r.stdout:
            self._out(r.stdout if r.stdout.endswith("\n") else r.stdout + "\n", "stdout")
        if r.stderr:
            self._out(r.stderr if r.stderr.endswith("\n") else r.stderr + "\n", "stderr")

    def interrupt(self):
        self._out("^C\n", "stderr")

    def stop(self):
        if self._alive:
            self._alive = False
            self._exit(None)

    @property
    def running(self):
        return self._alive


class MockBackend(Backend):
    name = "演示数据"

    def __init__(self):
        self._devices = list(D.DEVICES)
        self._packages = {p.name: p for p in D.PACKAGES}
        self._disabled: dict[int, set[str]] = {0: {p.name for p in D.PACKAGES if not p.enabled}, 10: set(), 999: set()}
        self._user_pkgs = {0: set(self._packages), **{u: set(v) for u, v in D.USER_PACKAGES.items()}}
        self._props = dict(D.PROPS)
        self._settings = {(ns, u): dict(v) for ns, v in D.SETTINGS.items() for u in ((None,) if ns == "global" else (0, 10, 999))}
        self._comp_off: set[tuple[int, str]] = set()
        self._state: dict[str, dict] = {}
        self._scrcpy: str | None = None
        self._lock = threading.Lock()

    # 每台设备的 root / verity / remount 状态
    def _st(self, serial) -> dict:
        return self._state.setdefault(serial, {"root": serial.startswith("emulator-"), "verity": True, "rw": False})

    # 环境 ----------------------------------------------------------
    def configure(self, adb_path, fastboot_path, scrcpy_path):
        self._scrcpy = scrcpy_path

    def tool_info(self):
        _sleep()
        return ToolInfo(r"C:\Android\platform-tools\adb.exe", "35.0.2", r"C:\Android\platform-tools\fastboot.exe",
                        "35.0.2-12147458", self._scrcpy, "3.3.3" if self._scrcpy else None, True)

    def restart_server(self):
        _sleep(0.6, 1.0)

    # 设备 ----------------------------------------------------------
    def _need(self, serial, adb=True) -> Device:
        d = next((x for x in self._devices if x.serial == serial), None)
        if d is None:
            raise DeviceNotFound(f"设备 {serial} 未连接")
        if d.state == "unauthorized":
            raise api.DeviceUnauthorized("设备未授权：请在手机上允许 USB 调试")
        if adb and not d.is_adb:
            raise CommandFailed(f"设备处于 {d.state} 状态，无法执行 adb 命令")
        return d

    def _need_root(self, serial, what: str):
        if not self._st(serial)["root"]:
            raise PermissionDenied(f"{what}需要 root：请先在设备总览点“Root”", f"{what}: Permission denied")

    def list_devices(self):
        _sleep(0.02, 0.06)
        with self._lock:
            return list(self._devices)

    def _build_type(self, serial) -> str:
        return "user" if serial.startswith("192.168.") else "userdebug"

    def device_detail(self, serial):
        d = self._need(serial)
        _sleep(0.2, 0.5)
        emu = d.transport == "emulator"
        p = self._props
        fps = {k.split(".")[1]: v for k, v in p.items() if k.startswith("ro.") and k.endswith(".build.fingerprint") and k.count(".") == 3}
        if emu:
            fps = {k: "google/sdk_gphone64_x86_64/emu64xa:15/AE3A.240806.005/12228598:userdebug/dev-keys" for k in fps}
        return DeviceDetail(
            serial=d.serial, model=d.model, manufacturer=d.manufacturer, brand=(d.manufacturer or "").lower(),
            device=d.product, android=d.android, api=d.api, build_id="AP4A.260905.002",
            fingerprint=f"google/{d.product}/{d.product}:{d.android}/AP4A.260905.002/12345678:{self._build_type(serial)}/test-keys",
            build_type=self._build_type(serial), build_date=p["ro.build.date"], security_patch="2026-09-05",
            vendor_security_patch=p["ro.vendor.build.security_patch"], baseband=None if emu else p["gsm.version.baseband"],
            bootloader=None if emu else p["ro.bootloader"],
            kernel="6.1.99-android14-11-g2d3c4a5b" if not emu else "6.6.30-android15-8-gd1e2f3",
            kernel_full="Linux version 6.1.99-android14-11-g2d3c4a5b (build-user@build-host) (Android (11368308, +pgo, +bolt, +lto, +mlgo) "
                        "clang version 18.0.1) #1 SMP PREEMPT Fri Sep 5 01:12:44 UTC 2026",
            abi="x86_64" if emu else "arm64-v8a", soc=None if emu else "Tensor G3",
            resolution="1080 × 2400" if emu else "1344 × 2992", density=420 if emu else 489,
            slot=None if emu else "_a", verified_boot="orange", selinux="Enforcing",
            uptime_s=1823 + int(time.time()) % 5000, mem_total=12 * GB, mem_available=int(4.7 * GB),
            battery=BatteryInfo(d.battery, "充电中" if d.transport == "usb" else "放电中", "USB" if d.transport == "usb" else None,
                                31.4, 4.12, 1240 if d.transport == "usb" else -380, "良好"),
            storage=[StorageInfo("/data", 256 * GB, int(182.4 * GB)), StorageInfo("/system", int(2.4 * GB), int(2.3 * GB))],
            partition_fingerprints=fps,
        )

    def list_apex_modules(self, serial):
        self._need(serial)
        _sleep(0.2, 0.4)
        return [ApexModule(n, v, f"/system/apex/{n}.{'capex' if v > 1 else 'apex'}") for n, v in D.APEX]

    def connect(self, address):
        _sleep(0.6, 1.2)
        if ":" not in address:
            address += ":5555"
        if address.startswith("10.0.0."):
            raise CommandFailed(f"无法连接到 {address}：连接超时", "failed to connect to '10.0.0.8:5555': Connection timed out")
        with self._lock:
            if not any(d.serial == address for d in self._devices):
                self._devices.append(Device(address, "device", "wifi", "Pixel 7", "Google", "panther", "14", 34, 51))
        return address

    def pair(self, address, code):
        _sleep(0.8, 1.4)
        if len(code) != 6 or not code.isdigit():
            raise CommandFailed("配对失败：配对码错误", "Failed: Wrong password or connection was dropped.")

    def disconnect(self, serial):
        _sleep()
        with self._lock:
            self._devices = [d for d in self._devices if d.serial != serial]

    def _bounce(self, serial, new_state: str | None, seconds: float):
        """设备短暂变为 connecting，seconds 秒后恢复为 new_state（None 表示从列表移除）。"""
        with self._lock:
            for i, x in enumerate(self._devices):
                if x.serial == serial:
                    self._devices[i] = Device(x.serial, "connecting", x.transport, x.model, x.manufacturer, x.product, x.android, x.api, x.battery)
                    if new_state is None:
                        del self._devices[i]

        def later():
            time.sleep(seconds)
            with self._lock:
                for j, x in enumerate(self._devices):
                    if x.serial == serial:
                        self._devices[j] = Device(x.serial, new_state, x.transport, x.model, x.manufacturer, x.product, x.android, x.api, x.battery)
        if new_state is not None:
            threading.Thread(target=later, daemon=True).start()

    def reboot(self, serial, mode="system"):
        d = next((x for x in self._devices if x.serial == serial), None)
        if d is None:
            raise DeviceNotFound(f"设备 {serial} 未连接")
        if d.is_fastboot and mode in ("sideload", "poweroff"):
            raise CommandFailed("Fastboot 模式下不支持该重启方式")
        _sleep(0.3, 0.6)
        st = self._st(serial)
        st["root"] = serial.startswith("emulator-")
        st["rw"] = False
        new_state = {"system": "device", "recovery": "recovery", "bootloader": "fastboot", "fastboot": "fastboot",
                     "sideload": "sideload"}.get(mode)
        self._bounce(serial, new_state, 3.0)

    def root_state(self, serial):
        self._need(serial)
        _sleep(0.05, 0.15)
        st = self._st(serial)
        return RootState(st["root"], st["rw"], self._build_type(serial), st["verity"])

    def root(self, serial, enable=True):
        self._need(serial)
        _sleep(0.3, 0.6)
        if self._build_type(serial) == "user":
            raise CommandFailed("user 版本固件不支持 adb root", "adbd cannot run as root in production builds")
        st = self._st(serial)
        if st["root"] == enable:
            return "adbd is already running as root" if enable else "adbd not running as root"
        st["root"] = enable
        if not enable:
            st["rw"] = False
        time.sleep(1.2)  # adbd 重启、设备重新出现
        return "restarting adbd as root" if enable else "restarting adbd as non root"

    def remount(self, serial):
        self._need(serial)
        st = self._st(serial)
        if not st["root"]:
            raise PermissionDenied("remount 需要 root", "Not running as root. Try \"adb root\" first.")
        _sleep(0.6, 1.0)
        if st["verity"]:
            st["verity"] = False
            return RemountResult("Disabling verity for /system\nUsing overlayfs for /system\nUsing overlayfs for /vendor\n"
                                 "Now reboot your device for settings to take effect", True)
        st["rw"] = True
        return RemountResult("Using overlayfs for /system\nUsing overlayfs for /vendor\nUsing overlayfs for /product\nremount succeeded", False)

    def disable_verity(self, serial):
        self._need(serial)
        self._need_root(serial, "disable-verity ")
        _sleep(0.3, 0.6)
        self._st(serial)["verity"] = False
        return "Successfully disabled verification\nNow reboot your device for settings to take effect"

    def send_key(self, serial, keycode):
        self._need(serial)
        _sleep(0.02, 0.08)

    def screenshot(self, serial):
        self._need(serial)
        _sleep(0.5, 0.9)
        return D.fake_png(270, 600)

    # 命令 ----------------------------------------------------------
    def shell(self, serial, command, timeout=30):
        self._need(serial)
        _sleep(0.02, 0.1)
        cmd = command.strip()
        head = cmd.split()[0] if cmd else ""
        root = self._st(serial)["root"]
        if head == "getprop":
            key = cmd[7:].strip()
            if key == "-Z":
                return ShellResult("".join(f"[{k}]: [{v}]\n" for k, v in sorted(self.get_prop_contexts(serial).items())), "", 0)
            if key:
                return ShellResult(self._props.get(key, "") + "\n", "", 0)
            return ShellResult("".join(f"[{k}]: [{v}]\n" for k, v in sorted(self._props.items())), "", 0)
        if head == "ls":
            path = cmd[2:].strip().split()[-1] if cmd[2:].strip() else "/"
            try:
                return ShellResult("\n".join(e.name for e in self.list_dir(serial, path)) + "\n", "", 0)
            except BackendError:
                return ShellResult("", f"ls: {path}: Permission denied", 1)
        if head == "uname":
            return ShellResult("Linux localhost 6.1.99-android14-11-g2d3c4a5b #1 SMP PREEMPT aarch64 Toybox\n", "", 0)
        if head == "getenforce":
            return ShellResult("Enforcing\n", "", 0)
        if head == "id":
            return ShellResult(("uid=0(root) gid=0(root)" if root else "uid=2000(shell) gid=2000(shell)") + "\n", "", 0)
        if cmd.startswith("cat /proc/cmdline"):
            return ShellResult("androidboot.hardware=husky androidboot.slot_suffix=_a androidboot.verifiedbootstate=orange "
                               "androidboot.selinux=enforcing androidboot.serialno=3A281FDH2000XY\n", "", 0)
        if head in ("rm", "mount", "touch") and "/system" in cmd and not self._st(serial)["rw"]:
            return ShellResult("", f"{head}: /system: Read-only file system", 1)
        if head == "dumpsys":
            parts = cmd.split()
            if "|" in cmd and "mCurrentFocus" in cmd:
                return ShellResult("  mCurrentFocus=Window{c3d4 u0 com.example.shop/com.example.shop.MainActivity}\n"
                                   "  mFocusedApp=ActivityRecord{d4e5f6 u0 com.example.shop/.MainActivity t1204}\n", "", 0)
            return ShellResult(self.dumpsys(serial, parts[1] if len(parts) > 1 else "battery"), "", 0)
        if cmd.startswith("service list"):
            svcs = self.list_binder_services(serial)
            return ShellResult(f"Found {len(svcs)} services:\n" + "".join(
                f"{i}\t{s.name}: [{s.interface or ''}]\n" for i, s in enumerate(svcs)), "", 0)
        if head == "lshal":
            return ShellResult("| All binderized services (registered with hwservicemanager)\nVINTF R Interface                         Thread Use Server Clients\n"
                               + "".join(f"Y     Y {n:<60} 0/1        {pid or 'N/A'}   {1532}\n" for n, _, pid, _ in D.HIDL_HALS), "", 0)
        if cmd.startswith("pm list users"):
            return ShellResult("Users:\n" + "".join(f"\tUserInfo{{{u}:{n}:c13}}{' running' if r else ''}\n" for u, n, _, r in D.USERS), "", 0)
        if cmd.startswith("pm list packages"):
            return ShellResult("".join(f"package:{p}\n" for p in sorted(self._packages)), "", 0)
        if cmd.startswith("settings list"):
            ns = cmd.split()[-1]
            return ShellResult("".join(f"{k}={v}\n" for k, v in sorted(self._settings.get((ns, None if ns == "global" else 0), {}).items())), "", 0)
        if head == "dmesg":
            if not root:
                return ShellResult("", "dmesg: klogctl: Permission denied", 1)
            return ShellResult("".join(f"[{1800 + i:5d}.{random.randint(0, 999999):06d}] {self._dmesg_text()}\n" for i in range(12)), "", 0)
        if head in ("start", "stop"):
            if not root:
                return ShellResult("", f"{head}: must be root", 1)
            return ShellResult("", "", 0)
        if head == "echo":
            return ShellResult(cmd[5:] + "\n", "", 0)
        if head in ("top", "ps"):
            return ShellResult("  PID USER         VIRT  RES  SHR S[%CPU] %MEM     TIME+ ARGS\n"
                               " 1532 system       21G 412M 210M S 12.0   3.4  21:03.11 system_server\n"
                               "23011 u0_a312      16G 288M 140M S  8.3   2.4   0:41.20 com.example.shop\n", "", 0)
        if head == "df":
            return ShellResult("Filesystem      1K-blocks     Used Available Use% Mounted on\n"
                               "/dev/block/dm-6   2502920  2412020     90900  97% /\n"
                               "/dev/block/dm-48 250000000 191260000 58740000  77% /data\n", "", 0)
        return ShellResult("", f"/system/bin/sh: {head}: inaccessible or not found", 127)

    def run_adb(self, serial, args, timeout=30):
        self._need(serial, adb=False)
        _sleep(0.05, 0.2)
        if not args:
            return ShellResult("", "adb: usage: no command specified", 1)
        cmd = args[0]
        if cmd == "shell":
            return self.shell(serial, " ".join(args[1:]), timeout)
        if cmd == "get-state":
            return ShellResult(next(d.state for d in self._devices if d.serial == serial) + "\n", "", 0)
        if cmd == "get-serialno":
            return ShellResult(serial + "\n", "", 0)
        if cmd == "reboot":
            self.reboot(serial, args[1] if len(args) > 1 else "system")
            return ShellResult("", "", 0)
        if cmd in ("root", "unroot"):
            try:
                return ShellResult(self.root(serial, cmd == "root") + "\n", "", 0)
            except CommandFailed as e:
                return ShellResult("", e.detail + "\n", 1)
        if cmd == "remount":
            try:
                return ShellResult(self.remount(serial).output + "\n", "", 0)
            except PermissionDenied as e:
                return ShellResult("", e.detail + "\n", 1)
        if cmd in ("pull", "push") and len(args) >= 3:
            time.sleep(0.6)
            return ShellResult(f"{args[1]}: 1 file {cmd}ed, 0 skipped. 38.2 MB/s (1204811 bytes in 0.030s)\n", "", 0)
        if cmd == "version":
            return ShellResult("Android Debug Bridge version 1.0.41\nVersion 35.0.2-12147458\n", "", 0)
        return ShellResult("", f"adb: unknown command {cmd}\n", 1)

    def start_command(self, serial, args, on_output, on_exit):
        self._need(serial)
        text = " ".join(args)

        def run(h: _Thread):
            code = 0
            k = 0
            while not h.wait(1.0 if "top" in text else 0.4):
                k += 1
                if "top" in text:
                    h.emit(on_output, f"Tasks: 812 total,   1 running, 811 sleeping\n"
                                      f"  PID USER  %CPU  RES  ARGS\n 1532 system {random.uniform(5, 15):4.1f} 412M system_server\n"
                                      f"  921 system {random.uniform(3, 9):4.1f} 118M surfaceflinger\n\n", "stdout")
                elif "logcat" in text:
                    h.emit(on_output, "\n".join(self._log_line(time.time()) for _ in range(3)) + "\n", "stdout")
                else:
                    r = self.run_adb(serial, args, 30)
                    h.emit(on_output, r.stdout, "stdout")
                    if r.stderr:
                        h.emit(on_output, r.stderr, "stderr")
                    code = r.exit_code
                    break
            on_exit(None if h.stopped else code, None)   # 被 stop() 终止时退出码为 None
        return _Thread(run)

    def open_shell(self, serial, on_output, on_exit):
        self._need(serial)
        _sleep(0.1, 0.2)
        return _MockShell(self, serial, on_output, on_exit)

    # Logcat --------------------------------------------------------
    def _log_line(self, t: float) -> str:
        lvl, tag, msg = random.choices(D.LOG_TEMPLATES, weights=[3, 6, 2, 1, 1, 5, 3, 2, 0.3, 3, 4, 2, 1, 1, 3])[0]
        pid = random.choice([1532, 23011, 921, 657, 1028, 2004])
        tid = pid + random.choice([0, 0, 0, 29, 56, 102])
        lt = time.localtime(t)
        stamp = time.strftime("%m-%d %H:%M:%S", lt) + f".{int((t % 1) * 1000):03d}"
        return f"{stamp} {pid:5d} {tid:5d} {lvl} {tag:<8}: " + msg.format(pid=pid, n=random.randint(1, 60))

    def _dmesg_text(self) -> str:
        return random.choice(D.DMESG).format(pid=random.randint(1000, 30000), n=random.randint(40, 48))

    def start_logcat(self, serial, buffers, on_lines, on_exit, tail=2000):
        self._need(serial)
        if buffers == ["dmesg"]:
            self._need_root(serial, "读取内核日志（dmesg）")

            def run_dmesg(h: _Thread):
                t0 = 1800.0
                h.emit(on_lines, [f"[{t0 + i * 0.37:11.6f}] {self._dmesg_text()}" for i in range(min(tail or 0, 60))])
                k = 0
                while not h.wait(random.uniform(0.3, 1.0)):
                    k += 1
                    h.emit(on_lines, [f"[{t0 + 30 + k:11.6f}] {self._dmesg_text()}"])
            return _Thread(run_dmesg, on_exit)

        def run(h: _Thread):
            now = time.time()
            n = min(tail or 0, 400)
            lines = [self._log_line(now - (n - i) * 0.05) for i in range(n)]
            if "kernel" in buffers and not self._st(serial)["root"]:
                lines.insert(0, "--------- toolbox: kernel 缓冲区不可用（需要 root），已忽略")
            h.emit(on_lines, lines)
            while not h.wait(random.uniform(0.05, 0.3)):
                batch = [self._log_line(time.time()) for _ in range(random.choice([1, 1, 2, 3, 8]))]
                if random.random() < 0.04:
                    pid = random.randint(1000, 3000)
                    batch.append(time.strftime("%m-%d %H:%M:%S.000") + f" {pid:5d} {pid:5d} W vendor.foo: type=1400 audit(0.0:212): "
                                 "avc: denied { read } for name=\"cfg\" dev=\"dm-5\" scontext=u:r:vendor_foo:s0 "
                                 "tcontext=u:object_r:vendor_data_file:s0 tclass=file permissive=0")
                h.emit(on_lines, batch)
        return _Thread(run, on_exit)

    def clear_logcat(self, serial, buffers):
        self._need(serial)
        _sleep()

    # 属性与 settings ------------------------------------------------
    def get_props(self, serial):
        self._need(serial)
        _sleep(0.2, 0.4)
        return dict(self._props)

    def get_prop_contexts(self, serial):
        self._need(serial)
        _sleep(0.2, 0.4)

        def ctx(k):
            for prefix, c in (("ro.build.", "build_prop"), ("ro.vendor.", "vendor_default_prop"), ("ro.boot.", "bootloader_prop"),
                              ("ro.product.", "build_prop"), ("persist.sys.", "system_prop"), ("init.svc.", "init_service_status_prop"),
                              ("sys.usb.", "usb_control_prop"), ("gsm.", "radio_prop"), ("debug.", "debug_prop"),
                              ("dalvik.vm.", "dalvik_prop"), ("vendor.", "vendor_default_prop")):
                if k.startswith(prefix):
                    return f"u:object_r:{c}:s0"
            return "u:object_r:default_prop:s0"
        return {k: ctx(k) for k in self._props}

    def set_prop(self, serial, key, value):
        self._need(serial)
        _sleep()
        if key.startswith("ro.") and key in self._props:
            raise PermissionDenied(f"无法设置只读属性 {key}", "Failed to set property 'ro...' (error code: 0x18)")
        self._props[key] = value

    def _settings_of(self, ns, user):
        key = (ns, None if ns == "global" else (user or 0))
        if key not in self._settings:
            raise CommandFailed(f"用户 {user} 不存在")
        return self._settings[key]

    def list_settings(self, serial, namespace, user=None):
        self._need(serial)
        _sleep(0.1, 0.3)
        return dict(self._settings_of(namespace, user))

    def put_setting(self, serial, namespace, key, value, user=None):
        self._need(serial)
        _sleep(0.05, 0.15)
        self._settings_of(namespace, user)[key] = value

    def delete_setting(self, serial, namespace, key, user=None):
        self._need(serial)
        _sleep(0.05, 0.15)
        self._settings_of(namespace, user).pop(key, None)

    # 服务与组件 ------------------------------------------------------
    def list_binder_services(self, serial):
        self._need(serial)
        _sleep(0.2, 0.4)
        return [BinderService(n, D.BINDER_INTERFACES[n] or None) for n in D.SERVICES]

    def dumpsys(self, serial, service, args=None, timeout=60):
        self._need(serial)
        _sleep(0.3, 0.9)
        if args and args[0] in ("-h", "--help"):
            return (f"{service} dump options:\n  [-h] [-a] [--proto] [--checkin]\n  -h: print this help.\n"
                    "  -a: include all available information.\n  --proto: output in protocol buffer format.\n")
        if service in D.DUMPSYS:
            return D.DUMPSYS[service]
        return f"DUMP OF SERVICE {service}{(' ' + ' '.join(args)) if args else ''}:\n  (演示数据：这个服务没有准备示例输出)\n"

    def list_init_services(self, serial):
        self._need(serial)
        _sleep(0.1, 0.3)
        return sorted((InitService(k[9:], v) for k, v in self._props.items() if k.startswith("init.svc.")), key=lambda s: s.name)

    def control_init_service(self, serial, name, action):
        self._need(serial)
        self._need_root(serial, f"{action} {name} ")
        key = "init.svc." + name
        if key not in self._props:
            raise CommandFailed(f"没有名为 {name} 的 init 服务")
        _sleep(0.3, 0.6)
        if action == "stop":
            self._props[key] = "stopped"
        else:
            self._props[key] = "restarting"

            def later():
                time.sleep(1.5)
                self._props[key] = "running"
            threading.Thread(target=later, daemon=True).start()

    def list_hals(self, serial):
        self._need(serial)
        _sleep(0.3, 0.6)
        out = [HalInfo(n, "hidl", t, pid, proc) for n, t, pid, proc in D.HIDL_HALS]
        out += [HalInfo(n, "aidl", None, *D.AIDL_HAL_PROCESS.get(n, (None, None))) for n in D.SERVICES if n.startswith("android.hardware.")]
        return sorted(out, key=lambda h: h.name)

    def hal_debug(self, serial, name, kind):
        self._need(serial)
        _sleep(0.3, 0.7)
        if kind == "hidl":
            return f"$ lshal debug {name}\n\n(演示数据) {name.split('::')[0]} debug dump:\n  state: ACTIVE\n  clients: 2\n  last error: none\n"
        return f"$ dumpsys {name}\n\n(演示数据) {name}\n  HAL version: 5\n  Boost hints supported: INTERACTION, LAUNCH, DISPLAY_UPDATE_IMMINENT\n"

    def list_processes(self, serial):
        self._need(serial)
        _sleep(0.2, 0.4)
        return [ProcessInfo(pid, ppid, u, n, rss * MB if rss else None, s) for pid, ppid, u, n, rss, s in D.PROCESSES]

    def process_detail(self, serial, pid):
        self._need(serial)
        _sleep(0.2, 0.4)
        row = next((p for p in D.PROCESSES if p[0] == pid), None)
        if row is None:
            raise CommandFailed(f"进程 {pid} 不存在（可能已退出）")
        _, ppid, user, name, rss, _ = row
        if not self._st(serial)["root"] and user not in ("shell",):
            status = f"Name:\t{name}\nPid:\t{pid}\nPPid:\t{ppid}\n(未 root：部分信息不可读)\n"
            return ProcessDetail(pid, name, name, status, [], [], None)
        nthreads = 4 + pid % 37
        threads = [(pid, name[:15])] + [(pid + i, n) for i, n in enumerate(
            ["Signal Catcher", "perfetto_hprof_", "ADB-JDWP Connec", "HeapTaskDaemon", "ReferenceQueueD", "FinalizerDaemon",
             "binder:%d_1" % pid, "binder:%d_2" % pid, "RenderThread", "hwuiTask0"][:nthreads - 1], start=1)]
        libs = ["/system/lib64/libc++.so", "/system/lib64/libutils.so", "/system/lib64/libbinder.so", "/system/lib64/liblog.so",
                "/system/lib64/libcutils.so", "/apex/com.android.runtime/lib64/bionic/libc.so", "/system/lib64/libhidlbase.so"]
        if "hardware" in name:
            libs += [f"/vendor/lib64/hw/{name.split('.')[2]}.zuma.so", "/vendor/lib64/libvendor_common.so"]
        if user.startswith("u") or name == "system_server":
            libs += ["/apex/com.android.art/lib64/libart.so", "/system/lib64/libandroid_runtime.so", "/system/lib64/libhwui.so"]
        rss_kb = (rss or 1) * 1024
        return ProcessDetail(pid, name, name if "/" in name else name, D.PROC_STATUS.format(
            name=name[:15], pid=pid, ppid=ppid, rss=rss_kb, anon=rss_kb * 2 // 3, file=rss_kb // 3, threads=nthreads),
            threads, sorted(libs), 40 + pid % 400)

    def dump_backtrace(self, serial, pid, timeout=30):
        self._need(serial)
        self._need_root(serial, "debuggerd ")
        _sleep(0.5, 1.0)
        name = next((p[3] for p in D.PROCESSES if p[0] == pid), "unknown")
        return D.BACKTRACE.format(pid=pid, name=name, tid1=pid + 7)

    def kill_process(self, serial, pid, signal=9):
        self._need(serial)
        _sleep()
        if pid < 1000 and not self._st(serial)["root"]:
            raise PermissionDenied(f"无法结束进程 {pid}：Operation not permitted（需要 root）")

    # 崩溃记录 -------------------------------------------------------
    def list_crash_files(self, serial):
        self._need(serial)
        _sleep(0.3, 0.6)
        rows = D.CRASHES if self._st(serial)["root"] else [c for c in D.CRASHES if c.kind == "dropbox"]
        return sorted(rows, key=lambda c: c.mtime or 0, reverse=True)

    def read_text(self, serial, path, max_bytes=2_000_000):
        self._need(serial)
        _sleep(0.2, 0.4)
        if "tombstone" in path.lower():
            return D.TOMBSTONE_TEXT
        if "anr" in path:
            return D.ANR_TEXT
        if "crash" in path:
            return ("Process: com.example.shop\nPID: 23011\nFlags: 0x38c83e44\nPackage: com.example.shop v31200 (3.12.0)\n\n"
                    "java.lang.IllegalStateException: Catalog not loaded\n"
                    "\tat com.example.shop.catalog.CatalogViewModel.requireItems(CatalogViewModel.kt:88)\n"
                    "\tat com.example.shop.catalog.CatalogFragment.onViewCreated(CatalogFragment.kt:41)\n")
        return "（演示数据）"

    def bugreport(self, serial, dest_dir, progress=None, cancel=None):
        d = self._need(serial)
        for i in range(0, 101, 2):
            if cancel and cancel.is_set():
                raise Cancelled("已取消")
            if progress:
                progress(Progress(i, "正在收集 dumpstate…" if i < 90 else "正在拉取 zip…"))
            time.sleep(0.12)
        return f"{dest_dir}/bugreport-{d.product}-AP4A.260905.002-2026-10-18-13-20-11.zip"

    # 应用 ----------------------------------------------------------
    def list_users(self, serial):
        self._need(serial)
        _sleep(0.1, 0.2)
        if serial.startswith("emulator-"):
            return [AndroidUser(0, "Owner", "主用户", True)]
        return [AndroidUser(*u) for u in D.USERS]

    def _user_ok(self, user):
        if user not in self._user_pkgs:
            raise CommandFailed(f"用户 {user} 不存在")

    def _as_user(self, p: PackageInfo, user: int) -> PackageInfo:
        uid = (p.uid % 100000 + user * 100000) if p.uid is not None else None
        return PackageInfo(p.name, p.version_name, p.version_code, p.system, p.name not in self._disabled.get(user, set()),
                           p.apk_path, uid, p.target_sdk)

    def list_packages(self, serial, user=0):
        self._need(serial)
        self._user_ok(user)
        _sleep(0.3, 0.6)
        return sorted((self._as_user(self._packages[n], user) for n in self._user_pkgs[user] if n in self._packages), key=lambda p: p.name)

    def package_detail(self, serial, package, user=0):
        self._need(serial)
        self._user_ok(user)
        _sleep(0.15, 0.35)
        p = self._packages.get(package)
        if p is None or package not in self._user_pkgs[user]:
            raise CommandFailed(f"用户 {user} 下找不到应用 {package}")
        perms = [Permission("android.permission.CAMERA", True, True), Permission("android.permission.ACCESS_FINE_LOCATION", False, True),
                 Permission("android.permission.POST_NOTIFICATIONS", True, True), Permission("android.permission.READ_MEDIA_IMAGES", False, True),
                 Permission("android.permission.RECORD_AUDIO", False, True), Permission("android.permission.INTERNET", True, False),
                 Permission("android.permission.ACCESS_NETWORK_STATE", True, False), Permission("android.permission.WAKE_LOCK", True, False),
                 Permission("android.permission.FOREGROUND_SERVICE", True, False)]
        users = sorted(u for u, pk in self._user_pkgs.items() if package in pk)
        return PackageDetail(self._as_user(p, user), 26, f"/data/user/{user}/{p.name}", "2026-08-02 10:11:42", "2026-10-15 21:03:09",
                             None if p.system else "com.android.vending", perms if not p.system else perms[5:], p.name != "android", users)

    def package_components(self, serial, package, user=0):
        self._need(serial)
        _sleep(0.15, 0.3)
        if package == "android":
            base = [("activity", "com.android.internal.app.ChooserActivity", True), ("activity", "com.android.internal.app.ResolverActivity", True),
                    ("service", "com.android.server.backup.FullBackupJob", False), ("receiver", "com.android.server.BootReceiver", False)]
            rows = [(k, n, e) for k, n, e in base]
        else:
            rows = [(k, package + suffix, e) for k, suffix, e in D.COMPONENT_TEMPLATES]
        return [Component(k, n, e, (user, n) not in self._comp_off and not n.endswith("DebugActivity"),
                          "android.permission.BIND_JOB_SERVICE" if "Sync" in n else None,
                          f"{package}.provider" if k == "provider" else None) for k, n, e in rows]

    def set_component_enabled(self, serial, component, enabled, user=0):
        self._need(serial)
        self._need_root(serial, "启用 / 停用组件")
        _sleep(0.1, 0.3)
        pkg, cls = component.split("/", 1)
        name = pkg + cls if cls.startswith(".") else cls
        (self._comp_off.discard if enabled else self._comp_off.add)((user, name))

    def dump_package(self, serial, package):
        self._need(serial)
        _sleep(0.3, 0.6)
        p = self._packages.get(package)
        if p is None:
            raise CommandFailed(f"找不到应用 {package}")
        users = sorted(u for u, pk in self._user_pkgs.items() if package in pk)
        comps = self.package_components(serial, package)
        acts = "".join(f"        {abs(hash(c.name)) % 0xfffffff:07x} {package}/{c.name.replace(package, '')}\n" for c in comps if c.kind == "activity")
        return (f"Activity Resolver Table:\n  Non-Data Actions:\n      android.intent.action.MAIN:\n{acts}\n"
                f"Packages:\n  Package [{package}] ({abs(hash(package)) % 0xfffffff:07x}):\n"
                f"    userId={p.uid}\n    pkg=Package{{{abs(hash(package)) % 0xfffffff:07x} {package}}}\n"
                f"    codePath={(p.apk_path or '').rsplit('/', 1)[0]}\n    resourcePath={(p.apk_path or '').rsplit('/', 1)[0]}\n"
                f"    legacyNativeLibraryDir=/data/app/{package}/lib\n    primaryCpuAbi=arm64-v8a\n"
                f"    versionCode={p.version_code} minSdk=26 targetSdk={p.target_sdk}\n    versionName={p.version_name}\n"
                f"    flags=[ {'SYSTEM ' if p.system else ''}HAS_CODE ALLOW_CLEAR_USER_DATA ALLOW_BACKUP ]\n"
                f"    timeStamp=2026-10-15 21:03:09\n    firstInstallTime=2026-08-02 10:11:42\n    lastUpdateTime=2026-10-15 21:03:09\n"
                f"    installerPackageName={'null' if p.system else 'com.android.vending'}\n"
                f"    signatures=PackageSignatures{{a1b2c3d4 version:3, signatures:[f0e1d2c3], past signatures:[]}}\n"
                f"    requested permissions:\n      android.permission.INTERNET\n      android.permission.CAMERA\n"
                f"      android.permission.POST_NOTIFICATIONS\n    install permissions:\n      android.permission.INTERNET: granted=true\n"
                + "".join(f"    User {u}: ceDataInode=1204{u} installed=true hidden=false suspended=false distractionFlags=0 stopped=false "
                          f"notLaunched=false enabled={0 if package not in self._disabled.get(u, set()) else 3} instant=false virtual=false\n"
                          f"      runtime permissions:\n        android.permission.CAMERA: granted=true, flags=[ USER_SET ]\n"
                          f"        android.permission.POST_NOTIFICATIONS: granted=true, flags=[ USER_SET ]\n" for u in users))

    def install(self, serial, apk_paths, options, progress=None, cancel=None):
        self._need(serial)
        for i in range(0, 101, 5):
            if cancel and cancel.is_set():
                raise Cancelled("已取消")
            if progress:
                progress(Progress(i if i < 100 else None, "正在推送…" if i < 100 else "正在安装…"))
            time.sleep(0.06)
        time.sleep(0.8)
        name = apk_paths[0].replace("\\", "/").rsplit("/", 1)[-1]
        if "old" in name:
            raise CommandFailed("安装失败：版本低于已安装版本，需勾选“允许降级”", "Failure [INSTALL_FAILED_VERSION_DOWNGRADE]")
        pkg = "com.example." + name.rsplit(".", 1)[0].replace("-", "_").lower()
        with self._lock:
            self._packages[pkg] = PackageInfo(pkg, "1.0.0", 1, False, True, f"/data/app/~~n/{pkg}-1/base.apk", 10400, 35)
            for u in ([options.user] if options.user is not None else list(self._user_pkgs)):
                self._user_pkgs.setdefault(u, set()).add(pkg)

    def uninstall(self, serial, package, keep_data=False, user=None):
        self._need(serial)
        _sleep(0.4, 0.8)
        p = self._packages.get(package)
        if p and p.system and user is None:
            raise CommandFailed("系统应用无法完全卸载，可以“为当前用户卸载”或停用", "Failure [DELETE_FAILED_INTERNAL_ERROR]")
        for u in ([user] if user is not None else list(self._user_pkgs)):
            self._user_pkgs.get(u, set()).discard(package)

    def clear_data(self, serial, package, user=0):
        self._need(serial)
        _sleep(0.3, 0.6)

    def force_stop(self, serial, package, user=0):
        self._need(serial)
        _sleep()

    def launch(self, serial, package, user=0):
        self._need(serial)
        _sleep(0.3, 0.6)

    def set_enabled(self, serial, package, enabled, user=0):
        self._need(serial)
        _sleep()
        (self._disabled.setdefault(user, set()).discard if enabled else self._disabled.setdefault(user, set()).add)(package)

    def set_permission(self, serial, package, permission, granted, user=0):
        self._need(serial)
        _sleep(0.05, 0.15)

    def extract_apk(self, serial, package, dest_dir, progress=None):
        self._need(serial)
        for i in range(0, 101, 10):
            if progress:
                progress(Progress(i, "正在拉取 base.apk"))
            time.sleep(0.08)
        return [f"{dest_dir}/{package}.apk"]

    # 文件 ----------------------------------------------------------
    def list_dir(self, serial, path):
        self._need(serial)
        _sleep(0.05, 0.2)
        path = "/" + path.strip("/") if path.strip("/") else "/"
        if path in D.FS and D.FS[path] is None and not self._st(serial)["root"]:
            raise PermissionDenied(f"无权访问 {path}（需要 root）", f"ls: {path}: Permission denied")
        names = D.FS.get(path)
        if names is None:
            names = ["app/", "local/", "misc/", "system/", "tombstones/", "anr/", "vendor/"] if path == "/data" else (
                ["placeholder.txt"] if path.count("/") > 2 else ["etc/", "lib/", "readme.txt"])
        out = []
        for n in names:
            is_dir, is_link = n.endswith("/"), n.endswith("@")
            name = n.rstrip("/@")
            full = (path.rstrip("/") + "/" + name)
            size = None if is_dir else (random.Random(name).randint(1, 900) * (MB if name.endswith((".zip", ".mp4", ".apk")) else 1024))
            out.append(FileEntry(name, full, is_dir or is_link, is_link, {"bin": "/system/bin", "etc": "/system/etc",
                                 "sdcard": "/storage/self/primary"}.get(name) if is_link else None, size,
                                 ("d" if is_dir else "l" if is_link else "-") + ("rwxrwx--x" if is_dir else "rw-rw----"),
                                 "root", "sdcard_rw" if path.startswith("/sdcard") else "root", "2026-10-18 13:16"))
        out.sort(key=lambda e: (not e.is_dir, e.name.lower()))
        return out

    def _transfer(self, total_items, progress, cancel, verb):
        steps = 30 * max(1, total_items)
        for i in range(steps + 1):
            if cancel and cancel.is_set():
                raise Cancelled("已取消")
            if progress:
                progress(Progress(int(i * 100 / steps), f"{verb} {i * 100 // steps}% · 38.2 MB/s"))
            time.sleep(0.03)

    def push(self, serial, local_paths, remote_dir, progress=None, cancel=None):
        self._need(serial)
        if remote_dir.startswith(("/system", "/vendor")) and not self._st(serial)["rw"]:
            raise PermissionDenied(f"{remote_dir} 只读：请先 Root + Remount", "remote couldn't create file: Read-only file system")
        self._transfer(len(local_paths), progress, cancel, "正在上传")
        lst = D.FS.setdefault("/" + remote_dir.strip("/"), [])
        if lst is not None:
            for p in local_paths:
                n = p.replace("\\", "/").rsplit("/", 1)[-1]
                if n not in lst:
                    lst.append(n)

    def pull(self, serial, remote_paths, local_dir, progress=None, cancel=None):
        self._need(serial)
        self._transfer(len(remote_paths), progress, cancel, "正在下载")
        return [local_dir.rstrip("/\\") + "/" + p.rsplit("/", 1)[-1] for p in remote_paths]

    def delete(self, serial, paths):
        self._need(serial)
        _sleep()
        for p in paths:
            parent, name = p.rsplit("/", 1)
            lst = D.FS.get(parent or "/")
            if lst:
                for n in list(lst):
                    if n.rstrip("/@") == name:
                        lst.remove(n)

    def mkdir(self, serial, path):
        self._need(serial)
        _sleep()
        parent, name = path.rstrip("/").rsplit("/", 1)
        lst = D.FS.setdefault(parent or "/", [])
        if lst is not None:
            lst.append(name + "/")

    def rename(self, serial, src, dst):
        self._need(serial)
        _sleep()
        parent, name = src.rsplit("/", 1)
        lst = D.FS.get(parent or "/")
        if lst:
            for i, n in enumerate(lst):
                if n.rstrip("/@") == name:
                    lst[i] = dst.rsplit("/", 1)[1] + ("/" if n.endswith("/") else "")

    # 投屏 ----------------------------------------------------------
    def start_scrcpy(self, serial, options, on_output, on_exit):
        self._need(serial)
        if not self._scrcpy:
            raise api.ToolNotFound("找不到 scrcpy：请在偏好设置里指定 scrcpy.exe 的位置",
                                   "演示数据：模拟未安装 scrcpy；在偏好设置里填写任意路径后可模拟启动")

        def run(h: _Thread):
            h.emit(on_output, "scrcpy 3.3.3 <https://github.com/Genymobile/scrcpy>\n", "stdout")
            time.sleep(0.3)
            h.emit(on_output, f"INFO: ADB device found:\nINFO:     -->   (usb)  {serial}  device  husky\n", "stdout")
            time.sleep(0.3)
            title = options.window_title or serial
            h.emit(on_output, f"INFO: Renderer: direct3d\nINFO: Texture: 1344x2992\nINFO: 窗口已打开：“{title}”（演示数据，未真正启动）\n", "stdout")
            while not h.wait(0.5):
                pass
        return _Thread(run, on_exit)
