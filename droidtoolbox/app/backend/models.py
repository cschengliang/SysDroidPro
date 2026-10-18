"""后端与界面之间传递的数据结构。只用标准库，界面和测试都不依赖 Qt。

约定：
- 字段缺失（取不到、设备不支持）一律用 None，不要用 0 或空串冒充。
- 大小单位为字节（int），时间为 Unix 秒（float）或设备原样字符串（字段注释里写明）。
- 所有 dataclass 都是只读快照，界面不会修改它们。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# adb devices / fastboot devices 报告的状态。界面按这些值显示状态点和中文状态词。
DeviceState = Literal["device", "offline", "unauthorized", "authorizing", "connecting",
                      "recovery", "fastboot", "sideload", "bootloader"]
Transport = Literal["usb", "wifi", "emulator"]
RebootMode = Literal["system", "recovery", "bootloader", "fastboot", "sideload", "poweroff"]
# "dmesg" 不是 logcat 缓冲区：表示改为读取内核日志（dmesg -w），需要 root
LogBuffer = Literal["main", "system", "crash", "events", "radio", "kernel", "all", "dmesg"]
SettingsNamespace = Literal["system", "secure", "global"]


@dataclass(frozen=True)
class ToolInfo:
    """本机工具链检测结果。路径找不到时为 None。"""
    adb_path: str | None
    adb_version: str | None          # 如 "35.0.2"
    fastboot_path: str | None
    fastboot_version: str | None
    scrcpy_path: str | None
    scrcpy_version: str | None
    server_running: bool


@dataclass(frozen=True)
class Device:
    """设备列表里的一项。离线 / 未授权 / fastboot 设备只保证 serial、state、transport 有值。"""
    serial: str
    state: DeviceState
    transport: Transport
    model: str | None = None          # ro.product.model，如 "Pixel 8 Pro"
    manufacturer: str | None = None   # ro.product.manufacturer，如 "Google"
    product: str | None = None        # ro.product.device / fastboot product，如 "husky"
    android: str | None = None        # ro.build.version.release，如 "15"
    api: int | None = None            # ro.build.version.sdk
    battery: int | None = None        # 电量百分比 0–100

    @property
    def display_name(self) -> str:
        return " ".join(x for x in (self.manufacturer, self.model) if x) or self.product or self.serial

    @property
    def is_adb(self) -> bool:
        """可以执行 adb shell 的状态。"""
        return self.state in ("device", "recovery")

    @property
    def is_fastboot(self) -> bool:
        return self.state in ("fastboot", "bootloader")


@dataclass(frozen=True)
class BatteryInfo:
    level: int | None                 # %
    status: str | None                # "充电中" / "放电中" / "已充满" / "未充电"
    plugged: str | None               # "AC" / "USB" / "无线" / None
    temperature_c: float | None       # 31.4
    voltage_v: float | None           # 4.12
    current_ma: int | None            # 正数为充电，负数为放电
    health: str | None                # "良好" 等


@dataclass(frozen=True)
class StorageInfo:
    mount: str                        # "/data"
    total: int                        # 字节
    used: int


@dataclass(frozen=True)
class DeviceDetail:
    """设备总览页的版本信息与状态。"""
    serial: str
    model: str | None
    manufacturer: str | None
    brand: str | None
    device: str | None                # ro.product.device
    android: str | None
    api: int | None
    build_id: str | None              # ro.build.id
    fingerprint: str | None           # ro.build.fingerprint
    build_type: str | None            # user / userdebug / eng
    build_date: str | None            # ro.build.date 原样
    security_patch: str | None        # ro.build.version.security_patch
    vendor_security_patch: str | None  # ro.vendor.build.security_patch
    baseband: str | None              # gsm.version.baseband
    bootloader: str | None            # ro.bootloader
    kernel: str | None                # uname -r
    kernel_full: str | None           # /proc/version
    abi: str | None                   # ro.product.cpu.abi
    soc: str | None                   # ro.soc.model 或 ro.board.platform
    resolution: str | None            # "1344 × 2992"
    density: int | None               # 489
    slot: str | None                  # "_a" / "_b" / None（非 A/B）
    verified_boot: str | None         # ro.boot.verifiedbootstate：green / yellow / orange / red
    selinux: str | None               # "Enforcing" / "Permissive"
    uptime_s: int | None
    mem_total: int | None             # 字节
    mem_available: int | None
    battery: BatteryInfo | None
    storage: list[StorageInfo] = field(default_factory=list)
    # 各分区构建指纹：{"system": "...", "vendor": "...", "product": "...", "odm": "...", "system_ext": "..."}，取不到的分区不放
    partition_fingerprints: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class RootState:
    """设备总览上 Root / Remount 按钮旁显示的状态。"""
    adbd_root: bool                   # adbd 是否以 root 运行（id -u == 0）
    system_writable: bool | None      # /system 是否可写（已 remount）；无法判断为 None
    build_type: str | None            # user / userdebug / eng；user 固件不能 adb root
    verity_enabled: bool | None       # dm-verity 是否开启；无法判断为 None


@dataclass(frozen=True)
class RemountResult:
    output: str                       # adb remount 的原始输出
    needs_reboot: bool                # 输出提示需要重启后生效（verity 被关闭 / overlayfs 首次启用）


@dataclass(frozen=True)
class ApexModule:
    name: str                         # com.android.wifi
    version_code: int | None
    path: str | None = None           # /system/apex/com.android.wifi.capex


@dataclass(frozen=True)
class ShellResult:
    stdout: str
    stderr: str
    exit_code: int


# ── 系统服务与组件 ────────────────────────────────────

@dataclass(frozen=True)
class BinderService:
    name: str                         # power
    interface: str | None             # android.os.IPowerManager（service list 方括号里的内容，可能为空）


@dataclass(frozen=True)
class InitService:
    name: str                         # surfaceflinger（init.svc.<name>）
    state: str                        # running / stopped / restarting / 其它原样


@dataclass(frozen=True)
class HalInfo:
    name: str                         # android.hardware.power@1.3::IPower/default 或 android.hardware.power.IPower/default
    kind: Literal["hidl", "aidl"]
    transport: str | None = None      # hwbinder / passthrough（HIDL）；AIDL 为 None
    pid: int | None = None            # 服务端进程，取不到为 None
    process: str | None = None        # 服务端进程名


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    ppid: int | None
    user: str
    name: str                         # 进程名 / 包名
    rss: int | None                   # 字节
    state: str | None = None          # R / S / D / Z ...


@dataclass(frozen=True)
class ProcessDetail:
    pid: int
    name: str
    cmdline: str
    status: str                       # /proc/<pid>/status 原文
    threads: list[tuple[int, str]] = field(default_factory=list)   # (tid, 线程名)
    libraries: list[str] = field(default_factory=list)             # maps 中去重后的 .so 路径
    fd_count: int | None = None


# ── 应用 ──────────────────────────────────────────────

@dataclass(frozen=True)
class AndroidUser:
    id: int                           # 0 / 10 / 999 ...
    name: str                         # 机主
    kind: str                         # "主用户" / "工作资料" / "分身" / "访客" / "其它"
    running: bool


@dataclass(frozen=True)
class PackageInfo:
    name: str
    version_name: str | None
    version_code: int | None
    system: bool
    enabled: bool                     # 在所查询的用户下是否启用
    apk_path: str | None
    uid: int | None = None
    target_sdk: int | None = None


@dataclass(frozen=True)
class Permission:
    name: str                         # android.permission.CAMERA
    granted: bool
    runtime: bool                     # 运行时权限才能在界面里授予 / 撤销


@dataclass(frozen=True)
class Component:
    kind: Literal["activity", "service", "receiver", "provider"]
    name: str                         # 完整类名 com.example.shop.MainActivity
    exported: bool | None
    enabled: bool                     # 在所查询的用户下是否启用
    permission: str | None = None     # 访问该组件需要的权限
    authority: str | None = None      # provider 的 authority


@dataclass(frozen=True)
class PackageDetail:
    info: PackageInfo
    min_sdk: int | None
    data_dir: str | None
    first_install: str | None         # dumpsys 原样字符串
    last_update: str | None
    installer: str | None
    permissions: list[Permission] = field(default_factory=list)
    launchable: bool = True
    installed_users: list[int] = field(default_factory=list)   # 该包安装于哪些用户


@dataclass(frozen=True)
class InstallOptions:
    replace: bool = True              # -r
    downgrade: bool = False           # -d
    grant_all: bool = False           # -g
    allow_test: bool = True           # -t
    user: int | None = None           # --user N；None 表示所有用户（adb install 默认行为）


@dataclass(frozen=True)
class CrashFile:
    kind: Literal["tombstone", "anr", "dropbox"]
    path: str                         # 设备上的绝对路径；dropbox 条目不是文件，写成 "dropbox:<tag>@<毫秒时间戳>"
    name: str
    mtime: float | None
    size: int | None
    process: str | None = None        # 能从文件头解析到的进程名
    summary: str | None = None        # 一行摘要，如 "signal 6 (SIGABRT)"


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str                         # 设备上的绝对路径
    is_dir: bool
    is_link: bool = False
    link_target: str | None = None
    size: int | None = None
    mode: str | None = None           # "drwxrwx--x"
    owner: str | None = None
    group: str | None = None
    mtime: str | None = None          # ls 原样字符串 "2026-10-18 13:16"


@dataclass(frozen=True)
class ScrcpyOptions:
    max_size: int | None = 1920
    bit_rate_mbps: int = 8
    max_fps: int | None = 60
    stay_awake: bool = True
    turn_screen_off: bool = False
    show_touches: bool = False
    always_on_top: bool = False
    record_path: str | None = None
    audio: bool = True
    window_title: str | None = None   # --window-title
    window_x: int | None = None       # --window-x / --window-y / --window-width / --window-height
    window_y: int | None = None
    window_width: int | None = None
    window_height: int | None = None


@dataclass(frozen=True)
class Progress:
    """长任务进度。percent 为 None 时界面显示不确定进度条。"""
    percent: int | None
    message: str = ""
