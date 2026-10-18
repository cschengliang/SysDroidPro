"""后端接口契约：界面只通过 Backend 调用设备能力。

实现要求（真实实现见 docs/BACKEND_SPEC.md，交给 adb.py 的 AdbBackend）：
- 纯 Python，不得导入 Qt。界面层负责把调用放进线程池、把回调转回主线程。
- 普通方法是同步阻塞调用，可以在任意工作线程里被并发调用，实现必须线程安全。
- start_* 方法立即返回 StreamHandle，在后台线程里持续调用回调；回调可能在任意线程触发。
- 长任务接受 progress 回调与 cancel 事件；cancel.is_set() 后应尽快终止子进程并抛 Cancelled。
- 失败一律抛 BackendError 的子类，message 用中文、可以直接展示给用户。
- user 参数是 Android 多用户 ID（0 为机主）；None 表示不加 --user（命令的默认行为）。
"""
from __future__ import annotations

import abc
import threading
from typing import Callable, Literal

from .models import (AndroidUser, ApexModule, BinderService, Component, CrashFile, Device, DeviceDetail, FileEntry,
                     HalInfo, InitService, InstallOptions, LogBuffer, PackageDetail, PackageInfo, ProcessDetail,
                     ProcessInfo, Progress, RebootMode, RemountResult, RootState, ScrcpyOptions, SettingsNamespace,
                     ShellResult, ToolInfo)

ProgressFn = Callable[[Progress], None]


# ── 错误 ──────────────────────────────────────────────

class BackendError(Exception):
    """所有后端错误的基类。str(e) 是给用户看的中文说明，detail 放原始输出便于排查。"""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.detail = detail


class ToolNotFound(BackendError):
    """adb / fastboot / scrcpy 找不到。"""


class DeviceNotFound(BackendError):
    """序列号对应的设备不在线。"""


class DeviceUnauthorized(BackendError):
    """设备未授权 USB 调试。"""


class CommandFailed(BackendError):
    """命令以非零退出码结束。"""

    def __init__(self, message: str, detail: str = "", exit_code: int | None = None):
        super().__init__(message, detail)
        self.exit_code = exit_code


class CommandTimeout(BackendError):
    """命令超时。"""


class Cancelled(BackendError):
    """用户取消。界面收到后不提示错误。"""


class PermissionDenied(BackendError):
    """需要 root 或权限不足。"""


# ── 流 ──────────────────────────────────────────────

class StreamHandle(abc.ABC):
    """持续运行的后台任务（logcat、持续运行的命令、scrcpy……）。"""

    @abc.abstractmethod
    def stop(self) -> None:
        """请求停止。可重复调用；返回后不应再触发数据回调（on_exit 除外）。"""

    @property
    @abc.abstractmethod
    def running(self) -> bool:
        ...


class ShellSession(StreamHandle):
    """持久的 adb shell 会话：cd、export、su 在后续命令中保持有效。"""

    @abc.abstractmethod
    def send(self, command: str) -> None:
        """写入一行命令（实现负责补换行）。"""

    @abc.abstractmethod
    def interrupt(self) -> None:
        """相当于 Ctrl+C：中断当前前台命令，会话保持。"""


# 流回调签名
LinesFn = Callable[[list[str]], None]                 # 一批原始文本行（不含换行符）
OutputFn = Callable[[str, str], None]                 # (文本, "stdout" | "stderr")
ExitFn = Callable[[BackendError | None], None]        # 流结束：正常结束 None，异常结束为错误
CommandExitFn = Callable[[int | None, BackendError | None], None]   # (退出码, 错误)；被 stop() 终止时退出码为 None


# ── 接口 ──────────────────────────────────────────────

class Backend(abc.ABC):
    """设备能力的全集。serial 参数均为 Device.serial。"""

    #: 界面在状态栏显示的后端名称，如 "adb" 或 "演示数据"
    name: str = "backend"

    # 环境 ----------------------------------------------------------
    @abc.abstractmethod
    def configure(self, adb_path: str | None, fastboot_path: str | None, scrcpy_path: str | None) -> None:
        """设置工具路径；None 表示从 PATH / ANDROID_HOME 自动查找。"""

    @abc.abstractmethod
    def tool_info(self) -> ToolInfo:
        ...

    @abc.abstractmethod
    def restart_server(self) -> None:
        """adb kill-server 后 start-server。"""

    # 设备 ----------------------------------------------------------
    @abc.abstractmethod
    def list_devices(self) -> list[Device]:
        """adb 与 fastboot 设备合并后的列表，界面每 2 秒轮询一次，应在 1 秒内返回。"""

    @abc.abstractmethod
    def device_detail(self, serial: str) -> DeviceDetail:
        ...

    @abc.abstractmethod
    def list_apex_modules(self, serial: str) -> list[ApexModule]:
        """pm list packages --apex-only --show-versioncode（加 -f 取路径），按名称排序。"""

    @abc.abstractmethod
    def connect(self, address: str) -> str:
        """adb connect host[:port]，返回连接后的 serial。"""

    @abc.abstractmethod
    def pair(self, address: str, code: str) -> None:
        """adb pair host:port code（Android 11+ 无线调试配对）。"""

    @abc.abstractmethod
    def disconnect(self, serial: str) -> None:
        ...

    @abc.abstractmethod
    def reboot(self, serial: str, mode: RebootMode = "system") -> None:
        """adb 与 fastboot 状态下都要能用；fastboot 状态下 sideload / poweroff 不可用时抛 CommandFailed。"""

    @abc.abstractmethod
    def root_state(self, serial: str) -> RootState:
        ...

    @abc.abstractmethod
    def root(self, serial: str, enable: bool = True) -> str:
        """adb root / adb unroot，返回 adbd 的输出。user 版本固件抛 CommandFailed。
        adbd 会重启、设备短暂断开；实现应等待设备重新出现（adb wait-for-device，最多 15 秒）再返回。"""

    @abc.abstractmethod
    def remount(self, serial: str) -> RemountResult:
        """adb remount。需要 root（未 root 抛 PermissionDenied）。输出提示需要重启时 needs_reboot=True。"""

    @abc.abstractmethod
    def disable_verity(self, serial: str) -> str:
        """adb disable-verity，返回输出。需要重启生效（由界面询问后调用 reboot）。"""

    @abc.abstractmethod
    def send_key(self, serial: str, keycode: str) -> None:
        """input keyevent，keycode 如 "KEYCODE_HOME"。"""

    @abc.abstractmethod
    def screenshot(self, serial: str) -> bytes:
        """exec-out screencap -p，返回 PNG 字节。"""

    # 命令 ----------------------------------------------------------
    @abc.abstractmethod
    def shell(self, serial: str, command: str, timeout: float = 30) -> ShellResult:
        """一次性执行 adb shell，不抛 CommandFailed（退出码放在结果里），超时抛 CommandTimeout。"""

    @abc.abstractmethod
    def run_adb(self, serial: str, args: list[str], timeout: float = 30) -> ShellResult:
        """执行 adb -s serial <args...>，如 ["reboot", "bootloader"]、["pull", "/sdcard/a.txt", "D:/"]。
        不抛 CommandFailed；超时抛 CommandTimeout。ADB 命令页的“adb 命令”类型用它。"""

    @abc.abstractmethod
    def start_command(self, serial: str, args: list[str], on_output: OutputFn, on_exit: CommandExitFn) -> StreamHandle:
        """持续运行的 adb -s serial <args...>（如 ["shell", "top -d 1"]），输出按块回调，stop() 终止进程。
        ADB 命令页勾选“持续运行”时使用。"""

    @abc.abstractmethod
    def open_shell(self, serial: str, on_output: OutputFn, on_exit: ExitFn) -> ShellSession:
        ...

    # Logcat --------------------------------------------------------
    @abc.abstractmethod
    def start_logcat(self, serial: str, buffers: list[LogBuffer], on_lines: LinesFn, on_exit: ExitFn,
                     tail: int | None = 2000) -> StreamHandle:
        """adb logcat -v threadtime -b ... -T tail。按 50–100ms 或 500 行攒一批再回调。
        buffers == ["dmesg"] 时改为读取内核日志（dmesg -w，需要 root），行原样转发。
        "kernel" 缓冲区需要 root；不支持时只对该缓冲区降级，不要让整个流失败。"""

    @abc.abstractmethod
    def clear_logcat(self, serial: str, buffers: list[LogBuffer]) -> None:
        ...

    # 属性与 settings ------------------------------------------------
    @abc.abstractmethod
    def get_props(self, serial: str) -> dict[str, str]:
        """getprop 全量。"""

    @abc.abstractmethod
    def get_prop_contexts(self, serial: str) -> dict[str, str]:
        """getprop -Z：{属性名: SELinux 上下文}，如 u:object_r:build_prop:s0。"""

    @abc.abstractmethod
    def set_prop(self, serial: str, key: str, value: str) -> None:
        """setprop；ro.* 属性设置失败时抛 PermissionDenied。"""

    @abc.abstractmethod
    def list_settings(self, serial: str, namespace: SettingsNamespace, user: int | None = None) -> dict[str, str]:
        """settings [--user N] list <namespace>；值为 null 的键保留为字符串 "null"。"""

    @abc.abstractmethod
    def put_setting(self, serial: str, namespace: SettingsNamespace, key: str, value: str, user: int | None = None) -> None:
        ...

    @abc.abstractmethod
    def delete_setting(self, serial: str, namespace: SettingsNamespace, key: str, user: int | None = None) -> None:
        ...

    # 服务与组件 ------------------------------------------------------
    @abc.abstractmethod
    def list_binder_services(self, serial: str) -> list[BinderService]:
        """service list（含接口名），按名字排序。"""

    @abc.abstractmethod
    def dumpsys(self, serial: str, service: str, args: list[str] | None = None, timeout: float = 60) -> str:
        ...

    @abc.abstractmethod
    def list_init_services(self, serial: str) -> list[InitService]:
        """由 getprop 中的 init.svc.* 得到，按名字排序。"""

    @abc.abstractmethod
    def control_init_service(self, serial: str, name: str, action: Literal["start", "stop", "restart"]) -> None:
        """start / stop <name>（需要 root）；restart = stop 后 start。"""

    @abc.abstractmethod
    def list_hals(self, serial: str) -> list[HalInfo]:
        """lshal（HIDL）+ service list 中的 android.hardware.*（AIDL）。"""

    @abc.abstractmethod
    def hal_debug(self, serial: str, name: str, kind: Literal["hidl", "aidl"]) -> str:
        """HIDL：lshal debug <name>；AIDL：dumpsys <name>。"""

    @abc.abstractmethod
    def list_processes(self, serial: str) -> list[ProcessInfo]:
        """ps -A -o PID,PPID,USER,RSS,S,NAME。"""

    @abc.abstractmethod
    def process_detail(self, serial: str, pid: int) -> ProcessDetail:
        """/proc/<pid>/status、cmdline、task/*/comm、maps（.so 去重）、fd 数量。进程不存在抛 CommandFailed。"""

    @abc.abstractmethod
    def dump_backtrace(self, serial: str, pid: int, timeout: float = 30) -> str:
        """debuggerd -b <pid>（需要 root）。"""

    @abc.abstractmethod
    def kill_process(self, serial: str, pid: int, signal: int = 9) -> None:
        ...

    # 崩溃记录 -------------------------------------------------------
    @abc.abstractmethod
    def list_crash_files(self, serial: str) -> list[CrashFile]:
        """/data/tombstones、/data/anr、dropbox 中的崩溃记录，按时间倒序。无权限的目录跳过。"""

    @abc.abstractmethod
    def read_text(self, serial: str, path: str, max_bytes: int = 2_000_000) -> str:
        """读取设备上的文本文件（cat / dropbox --print），超过 max_bytes 时截断并在末尾注明。"""

    @abc.abstractmethod
    def bugreport(self, serial: str, dest_dir: str, progress: ProgressFn | None = None,
                  cancel: threading.Event | None = None) -> str:
        """adb bugreport dest_dir，返回生成的 zip 路径。"""

    # 应用 ----------------------------------------------------------
    @abc.abstractmethod
    def list_users(self, serial: str) -> list[AndroidUser]:
        """pm list users，按 ID 排序。"""

    @abc.abstractmethod
    def list_packages(self, serial: str, user: int = 0) -> list[PackageInfo]:
        """该用户下安装的包，按包名排序。"""

    @abc.abstractmethod
    def package_detail(self, serial: str, package: str, user: int = 0) -> PackageDetail:
        ...

    @abc.abstractmethod
    def package_components(self, serial: str, package: str, user: int = 0) -> list[Component]:
        """Activity / Service / Receiver / Provider（dumpsys package 解析）。"""

    @abc.abstractmethod
    def set_component_enabled(self, serial: str, component: str, enabled: bool, user: int = 0) -> None:
        """pm enable / disable --user N <包名>/<类名>（需要 root）。"""

    @abc.abstractmethod
    def dump_package(self, serial: str, package: str) -> str:
        """dumpsys package <包名> 原始输出。"""

    @abc.abstractmethod
    def install(self, serial: str, apk_paths: list[str], options: InstallOptions,
                progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> None:
        """单个 APK 用 install，多个（split APK）用 install-multiple；options.user 不为 None 时加 --user。"""

    @abc.abstractmethod
    def uninstall(self, serial: str, package: str, keep_data: bool = False, user: int | None = None) -> None:
        """pm uninstall [-k] [--user N]。指定 user 时系统应用也能“为该用户卸载”。"""

    @abc.abstractmethod
    def clear_data(self, serial: str, package: str, user: int = 0) -> None:
        ...

    @abc.abstractmethod
    def force_stop(self, serial: str, package: str, user: int = 0) -> None:
        ...

    @abc.abstractmethod
    def launch(self, serial: str, package: str, user: int = 0) -> None:
        ...

    @abc.abstractmethod
    def set_enabled(self, serial: str, package: str, enabled: bool, user: int = 0) -> None:
        """pm enable / pm disable-user --user N。"""

    @abc.abstractmethod
    def set_permission(self, serial: str, package: str, permission: str, granted: bool, user: int = 0) -> None:
        ...

    @abc.abstractmethod
    def extract_apk(self, serial: str, package: str, dest_dir: str, progress: ProgressFn | None = None) -> list[str]:
        """拉取 base.apk 与 split APK，返回本地路径列表。"""

    # 文件 ----------------------------------------------------------
    @abc.abstractmethod
    def list_dir(self, serial: str, path: str) -> list[FileEntry]:
        """目录在前、名称不区分大小写排序；不含 . 与 ..。无权限抛 PermissionDenied。"""

    @abc.abstractmethod
    def push(self, serial: str, local_paths: list[str], remote_dir: str,
             progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> None:
        ...

    @abc.abstractmethod
    def pull(self, serial: str, remote_paths: list[str], local_dir: str,
             progress: ProgressFn | None = None, cancel: threading.Event | None = None) -> list[str]:
        """返回本地路径列表。"""

    @abc.abstractmethod
    def delete(self, serial: str, paths: list[str]) -> None:
        """rm -rf。"""

    @abc.abstractmethod
    def mkdir(self, serial: str, path: str) -> None:
        ...

    @abc.abstractmethod
    def rename(self, serial: str, src: str, dst: str) -> None:
        ...

    # 投屏 ----------------------------------------------------------
    @abc.abstractmethod
    def start_scrcpy(self, serial: str, options: ScrcpyOptions, on_output: OutputFn, on_exit: ExitFn) -> StreamHandle:
        """启动 scrcpy 独立窗口；用户关闭窗口时触发 on_exit(None)。"""
