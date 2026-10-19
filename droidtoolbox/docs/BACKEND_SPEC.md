# 后端实现规格：AdbBackend

> **v3（2026-10-18）**：契约按 `我的需求.txt` v3 更新。**第 0 节列出 v3 的增删改，优先于后文**；后文第 2 节中已删除方法（性能、录屏、fastboot 刷机、sideload）的说明可以忽略。始终以 `api.py` / `models.py` 为准。

## 0. v3 变更（优先于后文）

### 0.1 已删除（不要实现）

`start_perf`、`start_screenrecord`、`fastboot_vars`、`list_partitions`、`flash`、`erase`、`set_active_slot`、`boot_image`、`flashing_lock`、`sideload`、`list_services`（改为 `list_binder_services`）。`kill_process` 保留。

### 0.2 工具查找补充

在第 1.2 节的顺序中，**第 1 步（configure 路径）之后、第 2 步（PATH）之前**插入随程序附带的工具目录（相对 `droidtoolbox/` 根目录，即 `Path(app/backend/adb.py).parents[2]`）：

- adb / fastboot：`tool/platform-tools/`（本机为 adb 37.0.1）
- scrcpy：`tool/scrcpy-win64-v4.1/scrcpy.exe`（scrcpy 4.1）

scrcpy 自带一个 adb，启动 scrcpy 时必须设置环境变量 `ADB=<我们解析出的 adb 路径>`，避免两个版本的 adb 互相杀服务。

### 0.3 新增 / 修改的方法

| 方法 | 命令 | 解析与注意事项 |
| --- | --- | --- |
| `root_state` | 一条 shell：`id -u; getprop ro.build.type; getprop ro.boot.veritymode; getprop partition.system.verified; mount \| grep -E ' / \| /system '` | `adbd_root` = `id -u` 为 0；`build_type` 原样；`system_writable`：`/` 或 `/system` 挂载行包含 `rw,`（overlayfs 挂载也算 rw），取不到为 None；`verity_enabled`：`ro.boot.veritymode` 为 `enforcing` / `eio` → True，`disabled` → False，空 → 看 `partition.system.verified`，都取不到为 None |
| `root(enable)` | `adb root` / `adb unroot`，然后 `adb -s S wait-for-device`（最多 15 秒） | 输出含 `cannot run as root in production builds` → `CommandFailed("user 版本固件不支持 adb root")`；`already running as root` 视为成功。返回 adb 的原始输出 |
| `remount` | `adb remount` | 未 root → `PermissionDenied`（输出含 `Not running as root`）。返回 `RemountResult(output, needs_reboot)`；`needs_reboot` 当输出包含 `reboot` 且含 `take effect` / `Now reboot` / `reboot your device` / `Reboot the device`（大小写不敏感）。模拟器未以 `-writable-system` 启动时 remount 会失败，抛 `CommandFailed` 并在 message 里说明 |
| `disable_verity` | `adb disable-verity` | 返回输出；未 root → `PermissionDenied` |
| `list_apex_modules` | `pm list packages --apex-only --show-versioncode -f` | 行 `package:/system/apex/com.android.adbd.capex=com.android.adbd versionCode:351010000`；按**最后一个** `=` 拆路径与名称；按名称排序 |
| `run_adb(args)` | `adb -s S <args...>` | 不抛 `CommandFailed`，返回 `ShellResult`；超时抛 `CommandTimeout`。`args[0] == "shell"` 时等价于 `shell()` |
| `start_command(args, on_output, on_exit)` | `popen([adb, -s, S, *args])` | 按块读 stdout / stderr 回调 `on_output(text, stream)`；进程结束 `on_exit(退出码, None)`；被 `stop()` 终止时 `on_exit(None, None)`；设备断开 `on_exit(None, DeviceNotFound)` |
| `get_prop_contexts` | `getprop -Z` | 行 `[key]: [u:object_r:xxx_prop:s0]` |
| `list_settings(ns, user)` | `settings [--user N] list <ns>` | 每行 `key=value`，**按第一个 `=` 拆分**（值里可能有 `=`）；值 `null` 保留字符串 `"null"`。global 不加 `--user` |
| `put_setting` / `delete_setting` | `settings [--user N] put <ns> <key> <value>` / `settings [--user N] delete <ns> <key>` | value 为空串时传 `''` |
| `list_binder_services` | `service list` | 行 `12\tactivity: [android.app.IActivityManager]`，方括号为空时 `interface=None`；按名字排序（不区分大小写） |
| `list_init_services` | `getprop`（复用 `get_props`） | `init.svc.<name>` → `InitService(name, state)`；按名字排序 |
| `control_init_service(name, action)` | `start <name>` / `stop <name>`；restart = stop 后 start | 需要 root：未 root 时设备输出 `must be root` 或类似 → `PermissionDenied`；名称用 `shlex.quote` |
| `list_hals` | HIDL：`lshal --neat -itp`（不同版本参数可能不同，先在模拟器上确认，退回 `lshal -itp`）；AIDL：`service list` 中名称以 `android.hardware.` 开头的条目 | HIDL 的 `transport` 为 `hwbinder` / `passthrough`，`pid` 取 Server 列（`N/A` → None）；有 pid 的用一次 `ps -o PID,NAME -p <pid,...>` 补进程名。AIDL 的 pid / 进程名可用 `dumpsys --pid <name>`（Android 12+）获取，取不到为 None。按名称排序 |
| `hal_debug(name, kind)` | HIDL：`lshal debug <name>`；AIDL：`dumpsys <name>` | 原样返回，超时 30 秒 |
| `list_processes` | `ps -A -o PID,PPID,USER,RSS,S,NAME` | RSS 是 KB → 字节；跳过表头 |
| `process_detail(pid)` | 一条 shell，分段：`cat /proc/<pid>/status`、`cat /proc/<pid>/cmdline \| tr '\0' ' '`、`for t in /proc/<pid>/task/*; do echo "${t##*/} $(cat $t/comm)"; done`、`grep '\.so' /proc/<pid>/maps \| awk '{print $6}' \| sort -u`、`ls /proc/<pid>/fd \| wc -l` | 进程不存在 → `CommandFailed("进程 N 不存在（可能已退出）")`；未 root 读不到的段留空列表 / None，`status` 至少返回能读到的内容 |
| `dump_backtrace(pid)` | `debuggerd -b <pid>` | 需要 root；原样返回 |
| `list_users` | 优先 `cmd user list -v`（有 `type=full.SYSTEM` / `profile.MANAGED` / `profile.CLONE` / `full.GUEST` 等），失败退回 `pm list users`（`UserInfo{0:Owner:c13} running`） | `kind`：主用户 / 工作资料（MANAGED）/ 分身（CLONE）/ 访客（GUEST）/ 其它；`name` 为 null 时用 “机主”（id 0）或 “用户 N”；`running` 看 `running` 字样；按 id 排序 |
| `list_packages(user)` | `pm list packages -f -U --show-versioncode --user N`；系统集合 `pm list packages -s --user N`；停用集合 `pm list packages -d --user N` | `enabled` 指**该用户下**是否启用；`uid` 用输出中的 `uid:`（已是该用户的 uid） |
| `package_detail(pkg, user)` | `dumpsys package <pkg>` | 运行时权限取 `User <user>:` 段内的 `runtime permissions:`；`installed_users` 为所有 `User N: ... installed=true` 的 N；`data_dir` 若只有 user 0 的路径，替换为 `/data/user/<user>/<pkg>` |
| `package_components(pkg, user)` | `dumpsys package <pkg>`（先在 Android 16 模拟器上确认组件信息出现在哪些段落） | 可用来源：各 Resolver Table 段（带 intent-filter 的组件）、`Activities:` / `Services:` 等段（不同版本不同）、`User N:` 段中的 `disabledComponents:` / `enabledComponents:`。`exported` 取不到为 None；`enabled` 按该用户的 enabled/disabled 列表与默认值推断。**以真机验证为准，拿不到的字段留 None，不要编造** |
| `set_component_enabled(component, enabled, user)` | `pm enable --user N <pkg/cls>` / `pm disable --user N <pkg/cls>` | 需要 root（userdebug 下 shell 也可能有权限，以实际结果为准）；输出含 `new state:` 为成功 |
| `dump_package(pkg)` | `dumpsys package <pkg>` | 原样返回 |
| 包操作带 `user` | `install --user N`（`options.user` 为 None 时不加）；`pm uninstall [-k] [--user N]`；`pm clear --user N`；`am force-stop --user N`；`pm enable/disable-user --user N`；`pm grant/revoke --user N` | `launch(user)`：先 `cmd package resolve-activity --brief --user N -c android.intent.category.LAUNCHER <pkg>` 取组件（最后一行 `pkg/cls`），再 `am start --user N -n <组件>`；解析不到 → `CommandFailed("该应用没有可启动的界面")` |
| `start_logcat(["dmesg"])` | `adb shell dmesg -w`（需要 root） | 行原样转发（界面不解析）；未 root 时设备输出 `klogctl: Permission denied` → `on_exit(PermissionDenied)` |
| `start_scrcpy` 新选项 | `--window-title=<title>`、`--window-x=`、`--window-y=`、`--window-width=`、`--window-height=`（None 不加） | scrcpy 4.x 参数名以 `scrcpy --help` 为准；其余见 2.9 节 |
| `device_detail` 新字段 | 在 2.1 节的分段脚本里补 `cat /proc/version` 与 getprop | `build_date`=`ro.build.date`；`vendor_security_patch`=`ro.vendor.build.security_patch`；`baseband`=`gsm.version.baseband`；`bootloader`=`ro.bootloader`；`verified_boot`=`ro.boot.verifiedbootstate`；`kernel_full`=`/proc/version`；`partition_fingerprints`：`ro.<part>.build.fingerprint`（part ∈ system / system_ext / product / vendor / odm），缺的不放 |

### 0.3.1 运行时权限分类（v3.1 补充）

问题：`package_detail` 里，已申请但未授予的运行时权限（如 CAMERA）在 `dumpsys package` 中只出现在 `requested permissions:`，
不出现在 `runtime permissions:` 段，当前被归为 `runtime=False`，界面因此不显示授予开关。

要求：对 `requested permissions` 中既不在 install 段、也不在所选用户 runtime 段的权限，**查询设备上的权限定义**确定保护级别：
保护级别包含 `dangerous`（运行时权限）→ `Permission(name, granted=False, runtime=True)`；其余（normal / signature / 查不到定义）→ `runtime=False`。

- 实测注意：Android 16 模拟器上 `pm list permissions -d -u` 的 “Dangerous Permissions:” 段为空，CAMERA 不在其中，**不能依赖它**。
  先在模拟器上比较候选来源并选用可靠的一种：`dumpsys package permission <perm1> <perm2> ...`（看 `prot=` / `protectionLevel`）、
  `pm list permissions -f -g`（看 `protectionLevel:`）等；把采用的命令和输出样本记录到 fixtures 与 BACKEND_REPORT。
- 一次调用批量查询该包所有待分类权限，不要逐个权限起进程；结果按设备缓存（权限定义在设备运行期间不变，设备重新上线后失效）。
- 查询失败时退回原行为（`runtime=False`），不让整个 `package_detail` 失败。
- 已授予的运行时权限、install 权限的判定方式保持不变。

### 0.4 测试设备

本机有一台 Android 16（API 36）userdebug 模拟器 `emulator-5554`（x86_64，SELinux Enforcing，只有 user 0，adbd 默认非 root）。真机测试用它：`DTB_DEVICE=emulator-5554`。


界面已经完成，只通过 `app/backend/api.py` 中的 `Backend` 抽象类访问设备。本文档说明如何实现真实后端 `app/backend/adb.py` 里的 `AdbBackend`。

**契约以 `api.py` 与 `models.py` 为准**，本文档是补充说明。如果你认为契约需要改动（增删方法、改字段），先列出理由，不要直接改；界面依赖这些签名。

---

## 1. 总体要求

| 项目 | 要求 |
| --- | --- |
| 文件 | `app/backend/adb.py`（`class AdbBackend(Backend)`，`name = "adb"`）。建议另建 `app/backend/_proc.py`（子进程封装）与 `app/backend/parsers.py`（纯函数解析器，便于单测）。 |
| 依赖 | **只用标准库**。不得导入 PySide6 / PyQt6 / tbui（有测试检查）。运行环境是嵌入式 Python 3.14（`../python-3.14.8-embed-amd64`）。 |
| 线程 | 普通方法同步阻塞，会被界面线程池（16 线程）并发调用，实现必须线程安全；共享缓存加锁。 |
| 回调 | `start_*` / `open_shell` 立即返回句柄，在后台线程里调用回调。回调可能在任意线程触发；界面已负责转回主线程。**`stop()` 返回后不得再调用数据回调**（`on_exit` 除外），参考 `mock.py` 里 `_Thread.emit` 的加锁写法。 |
| 错误 | 一律抛 `api.py` 中的错误类型，`str(e)` 是可以直接给用户看的中文，`detail` 放原始输出最后几行。见第 4 节映射表。 |
| 取消 | 接受 `cancel: threading.Event` 的方法要轮询它；被设置后终止子进程并抛 `Cancelled`。 |
| 进度 | `progress(Progress(percent, message))`；拿不到百分比时 `percent=None`（界面显示不确定进度条），不要编造数字。 |
| Windows | 所有子进程加 `creationflags=subprocess.CREATE_NO_WINDOW`（程序用 `pythonw.exe` 启动，否则每条命令都会闪黑框）。 |
| 编码 | 输出按 UTF-8 解码，`errors="replace"`。二进制输出（截屏）用 `exec-out` 并保持 bytes。 |
| 引号 | 拼接设备端路径 / 参数一律 `shlex.quote`（设备是 sh），文件名可能含空格和中文。 |
| 引入方式 | `app/backend/__init__.py::create_backend()` 发现 `adb.py` 后自动使用它；`adb.py` 有语法 / 导入错误会直接抛出（不会静默退回演示数据）。`main.py --mock` 可强制演示数据。 |

### 1.1 可测试性：注入 runner

`AdbBackend.__init__(self, runner=None)`。runner 负责真正执行进程，测试时换成假的：

```python
class Runner(Protocol):
    def run(self, args: list[str], timeout: float, input: bytes | None = None) -> tuple[int, bytes, bytes]:
        """执行到结束，返回 (退出码, stdout, stderr)。超时抛 subprocess.TimeoutExpired。找不到程序抛 FileNotFoundError。"""
    def popen(self, args: list[str]) -> subprocess.Popen:
        """长期运行的进程（logcat、shell 会话、screenrecord、scrcpy、bugreport、push/pull 等）。stdin/stdout/stderr 均为 PIPE。"""
```

`args[0]` 是 `adb` / `fastboot` / `scrcpy` 的完整路径，由 `AdbBackend` 解析后填入。命令构造与输出解析都在 `AdbBackend` / `parsers.py` 中，这样测试只需要准备“命令 → 输出”的样本。

### 1.2 工具查找（`configure` / `tool_info`）

查找顺序（每个工具）：

1. `configure()` 传入的路径（设置页填写，非空时优先，文件不存在则视为未找到）；
2. `shutil.which("adb")`；
3. `%ANDROID_HOME%\platform-tools`、`%ANDROID_SDK_ROOT%\platform-tools`；
4. `%LOCALAPPDATA%\Android\Sdk\platform-tools`（Android Studio 默认位置，本机就在这里）。

fastboot 额外先找 adb 的同级目录。scrcpy 只查 1、2。

版本解析：

```
$ adb version
Android Debug Bridge version 1.0.41
Version 35.0.2-12147458
Installed as C:\...\adb.exe
→ adb_version = "35.0.2"

$ fastboot --version
fastboot version 35.0.2-12147458
→ fastboot_version = "35.0.2-12147458"

$ scrcpy --version
scrcpy 3.3.3 <https://github.com/Genymobile/scrcpy>
→ scrcpy_version = "3.3.3"
```

`server_running`：尝试连接 `127.0.0.1:5037`（0.3 秒超时），不要为了检测去启动服务。`tool_info` 结果可缓存，`configure` 后失效。

---

## 2. 方法实现说明

下面的命令都省略了 `adb -s <serial>` 前缀。**每条 adb 命令都必须带 `-s serial`**，否则多设备时会报错。

### 2.1 设备

#### `list_devices()` —— 每 2 秒轮询，必须 < 1 秒

```
$ adb devices -l
List of devices attached
3A281FDH2000XY         device usb:1-1 product:husky model:Pixel_8_Pro device:husky transport_id:3
192.168.1.23:5555      device product:houji model:23127PN0CC device:houji transport_id:4
adb-28011FDH-a1b2c3._adb-tls-connect._tcp device product:panther model:Pixel_7 device:panther transport_id:6
emulator-5554          device product:sdk_gphone64_x86_64 model:sdk_gphone64_x86_64 device:emu64xa transport_id:1
R5CW20ABCDE            unauthorized usb:1-2 transport_id:5
0123456789ABCDEF       offline transport_id:7

$ fastboot devices
28011FDH30016K	fastboot
```

- `model` 中下划线还原为空格。
- `transport`：`emulator-` 开头 → `emulator`；含 `:` 或 `._adb-tls-connect._tcp` → `wifi`；其余 `usb`。
- fastboot 设备 `state="fastboot"`，`product` 用 `fastboot -s S getvar product` 补（可缓存）。fastbootd 也报 `fastboot`，这是对的。
- 同一序列号同时出现在两边时以 adb 为准。
- `manufacturer` / `android` / `api` 不在 `devices -l` 里：对**新出现**的在线设备执行一次
  `shell getprop ro.product.manufacturer; getprop ro.build.version.release; getprop ro.build.version.sdk`
  （一条 shell 命令里用 `;` 串起来），按 serial 缓存，直到该设备离线或状态变化。
- `battery`：`shell dumpsys battery` 的 `level:`，缓存 30 秒。
- 补全信息的调用并行执行（线程池），单台超时 2 秒就放弃补全（字段留 None），**不要让一台卡住的设备拖慢整个列表**。
- `fastboot devices` 没有驱动时可能很慢，给 3 秒超时，超时当作没有 fastboot 设备。
- adb 服务没启动时 `adb devices` 会自动启动（输出 `* daemon started successfully`），忽略这些行。

#### `device_detail(serial)`

用**一条** `adb shell` 执行多个命令、以标记分段，减少往返：

```sh
echo ==PROPS; getprop
echo ==UNAME; uname -r
echo ==SELINUX; getenforce
echo ==MEM; head -n 3 /proc/meminfo
echo ==WM; wm size; wm density
echo ==UPTIME; cat /proc/uptime
echo ==BATTERY; dumpsys battery
echo ==DF; df -k /data /system 2>/dev/null
echo ==ID; id -u; command -v su
```

字段来源：

| 字段 | 来源 |
| --- | --- |
| model / manufacturer / brand / device | `ro.product.model` / `.manufacturer` / `.brand` / `.device` |
| android / api | `ro.build.version.release` / `ro.build.version.sdk` |
| build_id / fingerprint / build_type / security_patch | `ro.build.id` / `ro.build.fingerprint` / `ro.build.type` / `ro.build.version.security_patch` |
| abi | `ro.product.cpu.abi` |
| soc | `ro.soc.model`，没有则 `ro.board.platform` |
| slot | `ro.boot.slot_suffix`（非 A/B 设备为空 → None） |
| resolution / density | `wm size` → `Physical size: 1344x2992`（有 `Override size` 时显示 override 并在括号里注明）；`wm density` → `Physical density: 489`。分辨率字符串格式 `"1344 × 2992"`（乘号 U+00D7） |
| selinux | `getenforce` 原样 |
| rooted | `id -u` 输出 `0`（adbd 以 root 运行）或 `command -v su` 有输出 |
| uptime_s | `/proc/uptime` 第一个数取整 |
| mem_total / mem_available | `MemTotal` / `MemAvailable`（kB → 字节） |
| battery | 见下 |
| storage | `df -k` 各行（1K-blocks、Used，转字节），mount 取最后一列 |

`dumpsys battery`：

```
Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  status: 2
  health: 2
  level: 82
  voltage: 4122
  temperature: 314
```

- `status`：1 未知 / 2 充电中 / 3 放电中 / 4 未充电 / 5 已充满
- `health`：2 良好 / 3 过热 / 4 损坏 / 5 过压 / 7 过冷，其它 → None
- `plugged`：AC / USB / 无线 中为 true 的那个
- `temperature` 是 0.1 °C；`voltage` 是 mV → 4.12 V
- `current_ma`：`cat /sys/class/power_supply/battery/current_now`（µA，**正负号因厂商而异**：充电时应为正，若充电状态下读到负数则取反）。读不到为 None。

#### `connect(address)` / `pair(address, code)` / `disconnect(serial)`

- 地址没有端口时补 `:5555`。
- **adb connect 失败时退出码可能仍是 0**，必须看输出：
  - 成功：`connected to 192.168.1.23:5555`、`already connected to ...` → 返回 `host:port`
  - 失败：`failed to connect to ...`、`cannot connect to ...`、`unable to connect` → `CommandFailed("无法连接到 …：<原因>")`
- `adb pair host:port code`：成功输出含 `Successfully paired`，否则 `CommandFailed("配对失败：…")`。
- `adb disconnect serial`。

#### `reboot(serial, mode)`

| mode | adb 状态 | fastboot 状态 |
| --- | --- | --- |
| system | `adb reboot` | `fastboot reboot` |
| recovery | `adb reboot recovery` | `fastboot reboot recovery` |
| bootloader | `adb reboot bootloader` | `fastboot reboot bootloader` |
| fastboot | `adb reboot fastboot`（进 fastbootd） | `fastboot reboot fastboot` |
| sideload | `adb reboot sideload` | 不支持 → `CommandFailed` |
| poweroff | `adb shell reboot -p` | 不支持 → `CommandFailed` |

重启后设备会短暂消失 / 变成 offline，这是正常的，界面靠轮询感知。不要在 `reboot` 里等待设备回来。

#### `root(serial, enable)` / `remount(serial)`

- `adb root` 输出 `restarting adbd as root` 或 `adbd is already running as root` → 成功，返回输出；`adbd cannot run as root in production builds` → `CommandFailed("user 版本固件不支持 adb root")`。`adb unroot` 同理。
- `adb remount`：返回完整输出；输出含 `Now reboot your device` 也算成功（提示用户需要重启）；退出码非 0 → `CommandFailed`，常见原因 `verity` 未关闭，在 message 里提示“需先执行 adb disable-verity 并重启”。
- root 后 adbd 会重启，设备短暂断开，属正常。

#### `send_key(serial, keycode)`

`shell input keyevent <KEYCODE_xxx>`。

### 2.2 Shell

#### `shell(serial, command, timeout=30)`

`adb shell <command>`（整条命令作为一个参数传给 adb）。现代 adb 的 shell v2 协议会返回远端退出码。**不因非零退出码抛错**，返回 `ShellResult`；超时 → `CommandTimeout`；设备不在 → 第 4 节映射。

#### `open_shell(serial, on_output, on_exit)` → `ShellSession`

- `runner.popen([adb, "-s", S, "shell"])`，stdin 为管道（非 tty，远端是非交互 sh，没有提示符——界面自己显示提示符）。
- 两个读线程分别读 stdout / stderr，**按块读取**（`os.read` / `read1`，不要 `readline`，否则交互提示和不带换行的输出会卡住），解码后回调 `on_output(text, "stdout"|"stderr")`。UTF-8 多字节字符可能被切开，用 `codecs.getincrementaldecoder("utf-8")(errors="replace")`。
- `send(cmd)`：写入 `cmd + "\n"` 并 flush。
- `interrupt()`：管道里没法发 Ctrl+C。会话启动时先发送 `echo __DTB_PID__$$`，从输出里取到 sh 的 PID（这一行不要转发给界面），中断时另起一次性命令 `adb shell kill -INT -<pgid>` 或 `pkill -INT -P <pid>` 中断它的子进程。
- 用户输入 `su` 后进入 root shell（Magisk 可用）；输入 `exit` 退出 su 再 `exit` 才结束会话——这些都是远端行为，不需要特殊处理。
- 进程退出时调用 `on_exit(None)`；adb 报错（设备断开）则 `on_exit(DeviceNotFound(...))`。`stop()` 关闭 stdin 并终止进程。

### 2.3 Logcat

#### `start_logcat(serial, buffers, on_lines, on_exit, tail=2000)`

```
adb -s S logcat -v threadtime -b main -b system -b crash -T 2000
```

- `buffers` 里的每一项转成一个 `-b`；`["all"]` → `-b all`。
- `tail`：`None` → 不加 `-T`（输出整个缓冲区）；`0` → 只要新日志（`-T 1`，或用设备当前时间 `-T "MM-DD hh:mm:ss.000"`）；`N` → `-T N`。
- 读线程按行读，攒批：**每 50–100 ms 或满 500 行回调一次** `on_lines(list[str])`（行不含换行符）。高峰期 5000 行/秒也不能卡。
- `kernel` 缓冲区在非 root 设备上会失败（`Unable to open log device` / `Invalid buffer`）。处理方式：去掉 kernel 重新启动 logcat，并先回调一行
  `--------- toolbox: kernel 缓冲区不可用（需要 root），已忽略`
  （以 `--------- ` 开头但不是 `--------- beginning of` 的行，界面会原样显示）。
- 设备断开时 logcat 进程退出：`on_exit(DeviceNotFound("设备已断开"))`；`stop()` 主动停止时 `on_exit(None)`。
- 界面负责解析 threadtime 格式（`tbui.LogRow.parse`），后端不要解析，原样转发。

#### `clear_logcat(serial, buffers)`

`adb logcat -c -b ...`（同样每个缓冲区一个 `-b`）。

### 2.4 性能

#### `start_perf(serial, on_sample, on_exit, interval=1.0, package=None, top=30)`

每个周期执行**一条** shell 命令采集全部指标（避免多次往返）：

```sh
echo ==STAT; head -n 32 /proc/stat
echo ==FREQ; cat /sys/devices/system/cpu/cpu[0-9]*/cpufreq/scaling_cur_freq 2>/dev/null
echo ==MEM; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo
echo ==BAT; cat /sys/class/power_supply/battery/temp /sys/class/power_supply/battery/current_now 2>/dev/null
echo ==THERMAL; for z in /sys/class/thermal/thermal_zone*; do echo "$(cat $z/type 2>/dev/null)=$(cat $z/temp 2>/dev/null)"; done
echo ==TOP; top -b -n 1 -m <top>
echo ==GFX; dumpsys gfxinfo <package>        # 仅 package 非空时
```

| 指标 | 计算 |
| --- | --- |
| `cpu_total` / `cpu_cores` | `/proc/stat` 的 `cpu` 与 `cpuN` 行，**与上一帧做差**：`busy = total - idle - iowait`，`% = Δbusy / Δtotal × 100`。第一帧没有上一帧 → `cpu_total=None`、`cpu_cores=[]`。离线核心（行缺失）记 0。 |
| `cpu_freq_mhz` | `scaling_cur_freq` 是 kHz → ÷1000 |
| `mem_total` / `mem_available` | kB → 字节 |
| `battery_temp_c` | `temp` 是 0.1 °C |
| `battery_current_ma` | µA → mA，符号规则同 2.1 |
| `thermal` | `type=temp`，temp 一般是毫度（>1000 时 ÷1000）。只保留数值合理（0–150 °C）的，按名称去重。 |
| `processes` | toybox `top` 输出：**按表头定位列**（不同版本列不同）。`%CPU` 是以单核 100% 计的（表头里有 `800%cpu` 表示 8 核），转换为占全部核心的比例：`cpu / 核心数`，裁剪到 0–100。`RES` 带单位（`412M`、`1.2G`、`88K`）转字节。线程数没有就 None。按 CPU 降序取前 `top` 个。 |
| `fps` / `jank_pct` | `dumpsys gfxinfo <pkg>` 的 `Total frames rendered: N` 与 `Janky frames: M (x%)`，**与上一帧做差**：`fps = ΔN / Δt`，`jank = ΔM / ΔN × 100`（ΔN 为 0 时 fps=0、jank=None）。应用不在前台 / 进程不存在时为 None。 |

- 采样耗时超过 interval 时不要堆积，直接进入下一轮。
- 任一指标采集失败只让该字段为 None，不要中断整个流；设备断开才 `on_exit(DeviceNotFound)`。

#### `kill_process(serial, pid, signal=9)`

`shell kill -<signal> <pid>`；输出 `Operation not permitted` → `PermissionDenied("无法结束进程 …（需要 root）")`。

### 2.5 系统属性与服务

- `get_props`：`shell getprop`，每行 `[key]: [value]`。**值可能跨多行**（以 `[` 开头的行才是新条目，其余行拼到上一项的值里）。
- `set_prop`：`shell setprop <key> <value>`（value 为空串时传 `''`）。失败输出 `Failed to set property` 或退出码非 0；key 以 `ro.` 开头时抛 `PermissionDenied("无法设置只读属性 …")`，其它 → `CommandFailed`。
- `list_services`：`shell dumpsys -l`，第一行 `Currently running services:` 之后每行一个服务名（去掉缩进），**按名字排序**（不区分大小写）。
- `dumpsys(serial, service, args, timeout)`：`shell dumpsys <service> <args...>`；超时 → `CommandTimeout`。输出原样返回。

### 2.6 崩溃记录与 Bug 报告

#### `list_crash_files(serial)`

| 来源 | 命令 | 说明 |
| --- | --- | --- |
| tombstone | `shell ls -l /data/tombstones` | user 版本无 root 时没有权限 → 跳过。跳过 `.pb` 文件。`process`/`summary` 可以读文件头前 4 KB 解析 `Cmdline:` 与 `signal …` 行（选做，可只对最近 20 个做）。 |
| ANR | `shell ls -l /data/anr` | 同上；`process` 从文件头 `Cmd line:` 解析，`summary` 用 `Subject:` 行。 |
| dropbox | `shell dumpsys dropbox` | 解析条目行 `2026-10-18 13:16:03 data_app_crash (text, 4120 bytes)`。只保留崩溃相关 tag：`*_crash`、`*_anr`、`*_wtf`、`SYSTEM_TOMBSTONE`、`*_native_crash`、`SYSTEM_LAST_KMSG`、`SYSTEM_BOOT`。`path` 写成 `dropbox:<tag>@<毫秒时间戳>`。`process`/`summary` 从紧随的 `Process:` 行与异常首行解析。 |

`mtime` 转成 Unix 秒（设备时区：用 `date +%z` 取一次偏移）。按时间倒序返回。任何来源失败都只跳过该来源。

#### `read_text(serial, path, max_bytes=2_000_000)`

- 普通文件：`shell cat <path>`（或 `exec-out` 后截断）。
- `dropbox:` 路径：`shell dumpsys dropbox --print <tag>`，按时间戳挑出对应条目。
- 超过 `max_bytes` 时截断，并在末尾追加一行 `…（已截断，原文件 N 字节）`。

#### `bugreport(serial, dest_dir, progress, cancel)`

`adb -s S bugreport <dest_dir>`，读 stdout/stderr 的进度行（形如 `[ 45%] generating bugreport-....zip`，用 `\[\s*(\d+)%\]` 解析；行可能以 `\r` 分隔）。最后一行通常是 `Bug report copied to <path>`，返回该路径；找不到就在 `dest_dir` 里取最新的 `bugreport-*.zip`。耗时 1–3 分钟，不要设短超时。取消 → 终止进程并抛 `Cancelled`。

### 2.7 应用

#### `list_packages(serial)`

一次拿全：

```
$ adb shell pm list packages -f -U --show-versioncode
package:/data/app/~~Ab12==/com.example.shop-Cd34==/base.apk=com.example.shop versionCode:31200 uid:10312
package:/system/priv-app/Settings/Settings.apk=com.android.settings versionCode:35 uid:1000
```

- **注意 apk 路径与包名之间的 `=`**：路径本身可能包含 `=`（`~~Ab12==`），要用最后一个 `.apk=` 分割。
- `system`：`pm list packages -s` 的集合；`enabled`：不在 `pm list packages -d` 中。
- `version_name` / `target_sdk`：用一次 `dumpsys package packages` 批量解析（`Package [com.x] (hash):` 段落内的 `versionName=`、`targetSdk=`）。拿不到就 None（界面显示“—”）。
- 按包名排序返回。

#### `package_detail(serial, package)`

`shell dumpsys package <pkg>`，解析该包的段落：

| 字段 | 位置 |
| --- | --- |
| versionName / versionCode / minSdk / targetSdk | `versionName=3.12.0`；`versionCode=31200 minSdk=26 targetSdk=35` |
| data_dir | `dataDir=` |
| first_install / last_update | `firstInstallTime=` / `lastUpdateTime=`（原样字符串） |
| installer | `installerPackageName=` |
| permissions | `requested permissions:` 列表为全集；`install permissions:` 中 `xxx: granted=true` 为安装时权限（`runtime=False`）；`runtime permissions:`（在 `User 0:` 段内）中为运行时权限（`runtime=True`，granted 按值）。请求了但两处都没有的，按 `runtime=False, granted=False`。 |
| launchable | `cmd package resolve-activity --brief -c android.intent.category.LAUNCHER <pkg>`，输出 `No activity found` → False |

包不存在 → `CommandFailed("找不到应用 …")`。

#### 安装与操作

| 方法 | 命令 | 成功判断 / 错误 |
| --- | --- | --- |
| `install` | 单个：`adb install [-r] [-d] [-g] [-t] <apk>`；多个：`adb install-multiple ...` | 输出含 `Success`。失败输出 `Failure [INSTALL_FAILED_XXX: …]`，按下表翻译成中文放进 message，原文放 detail。 |
| `uninstall` | `shell pm uninstall [-k] <pkg>` | `Success`；`DELETE_FAILED_INTERNAL_ERROR`（系统应用）→ `CommandFailed("系统应用无法卸载，可以改为停用")` |
| `clear_data` | `shell pm clear <pkg>` | `Success` |
| `force_stop` | `shell am force-stop <pkg>` | 退出码 0 |
| `launch` | `shell monkey -p <pkg> -c android.intent.category.LAUNCHER 1` | 输出含 `No activities found to run` → `CommandFailed("该应用没有可启动的界面")` |
| `set_enabled` | `shell pm enable <pkg>` / `shell pm disable-user --user 0 <pkg>` | 输出含 `new state: enabled` / `disabled-user` |
| `set_permission` | `shell pm grant <pkg> <perm>` / `pm revoke` | 退出码 0；非运行时权限会报 `not a changeable permission type` → `CommandFailed` |
| `extract_apk` | `shell pm path <pkg>` 得到 `package:/…/base.apk` 与 `split_*.apk`，逐个 `adb pull` | base → `<pkg>.apk`，split → `<pkg>-<split名>.apk`；返回本地路径列表 |

`install` 进度：adb 输出管道时没有百分比。做法：先 `progress(Progress(None, "正在推送并安装…"))`；如果想要百分比，可改为先 `adb push` 到 `/data/local/tmp/`（用 2.8 的方法算进度），再 `shell pm install`，最后删除临时文件。

安装错误翻译（至少覆盖）：

| 代码 | message |
| --- | --- |
| `INSTALL_FAILED_VERSION_DOWNGRADE` | 版本低于已安装版本，需勾选“允许降级” |
| `INSTALL_FAILED_UPDATE_INCOMPATIBLE` | 签名与已安装版本不一致，需先卸载旧版本 |
| `INSTALL_FAILED_ALREADY_EXISTS` | 应用已存在，需勾选“覆盖安装” |
| `INSTALL_FAILED_INSUFFICIENT_STORAGE` | 设备存储空间不足 |
| `INSTALL_FAILED_OLDER_SDK` | 设备系统版本低于应用要求的 minSdk |
| `INSTALL_FAILED_NO_MATCHING_ABIS` | 应用不支持该设备的 CPU 架构 |
| `INSTALL_FAILED_TEST_ONLY` | 测试包，需勾选“允许测试包” |
| `INSTALL_PARSE_FAILED_NO_CERTIFICATES` | APK 未签名 |
| `INSTALL_FAILED_USER_RESTRICTED` | 用户拒绝了安装（检查设备上的“通过 USB 安装”开关） |
| `INSTALL_FAILED_INVALID_APK` / `INSTALL_PARSE_FAILED_*` | APK 文件无效或已损坏 |
| 其它 | 安装失败：<代码> |

### 2.8 文件

#### `list_dir(serial, path)`

`shell ls -la <path>/`（**末尾加 `/`**，否则 `/sdcard` 这种符号链接只会列出链接本身）。toybox 输出：

```
total 64
drwxrwx--x  4 root sdcard_rw 3452 2026-10-18 13:16 Android
-rw-rw----  1 root sdcard_rw 488012 2026-10-18 13:16 screenrecord 20261018.mp4
lrw-r--r--  1 root root        11 2009-01-01 08:00 bin -> /system/bin
```

- 按固定字段切分：权限、链接数、owner、group、size、日期、时间，剩下的是名称（**名称可以含空格**）；链接再按 ` -> ` 拆出 `link_target`。设备节点的 size 列是 `主, 次`，size 记 None。
- 跳过 `total`、`.`、`..`。
- 链接是否指向目录：对所有链接用一条命令批量判断（如 `for f in a b c; do [ -d "$f" ] && echo "$f"; done`），指向目录的 `is_dir=True`（界面才能双击进入）。
- `ls: <path>: Permission denied` → `PermissionDenied("无权访问 …（需要 root）")`；`No such file or directory` → `CommandFailed("路径不存在：…")`。
- 排序：目录在前，名称不区分大小写。

#### `push` / `pull`

`adb push <local...> <remote_dir>/`、`adb pull <remote...> <local_dir>`。

**进度**：adb 输出到管道时不保证有百分比。可靠的做法是并行轮询已传输的大小：
- push：传输前算出本地总字节数，传输中每 300 ms 执行 `shell stat -c %s <远端文件>`（目录用 `du -sb`）；
- pull：传输前用 `shell stat -c %s` / `du -sb` 拿到远端总大小，传输中统计本地已写入字节数；
- 消息里带速度，如 `正在上传 45% · 38.2 MB/s`。

取消 → 终止进程、删除不完整的目标文件（push 时删远端，pull 时删本地），抛 `Cancelled`。`pull` 返回本地路径列表。

#### `delete` / `mkdir` / `rename`

`shell rm -rf -- <paths...>`、`shell mkdir -p -- <path>`、`shell mv -- <src> <dst>`。输出含 `Permission denied` / `Read-only file system` → `PermissionDenied`。

### 2.9 截屏、录屏、投屏

- `screenshot`：`adb exec-out screencap -p`，返回 bytes；不以 PNG 魔数开头 → `CommandFailed`。
- `start_screenrecord(serial, local_path, options, on_exit)`：
  1. `options.show_touches` 为真时先 `shell settings put system show_touches 1`，结束后恢复原值（先 `settings get` 保存）。
  2. 远端文件 `/sdcard/dtb_record_<时间戳>.mp4`，`popen([adb, -s, S, shell, screenrecord, --bit-rate, <Mbps*1000000>, (--size WxH), --time-limit, T, 远端文件])`。
  3. `stop()`：**必须向设备上的 screenrecord 发 SIGINT**（`shell pkill -INT screenrecord`），直接杀本地 adb 进程会得到损坏的 mp4。
  4. 进程结束后等 0.5–1 秒让文件落盘，`adb pull` 到 `local_path`，删除远端文件，调用 `on_exit(None)`；任何一步失败 `on_exit(错误)`。到达时长上限自然结束时同样拉取。
- `start_scrcpy(serial, options, on_output, on_exit)`：scrcpy 未找到 → 抛 `ToolNotFound("找不到 scrcpy：请在设置里指定 scrcpy.exe 的位置")`。参数：

  | 选项 | 参数 |
  | --- | --- |
  | serial | `-s S` |
  | max_size | `--max-size=N`（None 不加） |
  | bit_rate_mbps | `--video-bit-rate=8M` |
  | max_fps | `--max-fps=N`（None 不加） |
  | stay_awake / turn_screen_off / show_touches / always_on_top | `--stay-awake` / `--turn-screen-off` / `--show-touches` / `--always-on-top` |
  | record_path | `--record=<path>` |
  | audio=False | `--no-audio` |

  设置环境变量 `ADB=<adb 路径>`，让 scrcpy 用同一个 adb（版本不一致会互相杀服务）。输出转发给 `on_output`，进程退出：退出码 0 → `on_exit(None)`，否则 `on_exit(CommandFailed(最后一行输出))`。

### 2.10 Fastboot 与 Sideload

`fastboot` 的输出都在 **stderr**。

#### `fastboot_vars(serial)`

```
$ fastboot -s S getvar all
(bootloader) max-download-size:0x10000000
(bootloader) partition-size:boot_a:0x4000000
(bootloader) partition-type:boot_a:raw
(bootloader) is-logical:system_a:yes
(bootloader) current-slot:a
(bootloader) unlocked:yes
...
all: Done!!
Finished. Total time: 0.050s
```

- 去掉 `(bootloader) ` 前缀（fastbootd 下可能没有前缀）。
- **键本身可能含 `:`**（`partition-size:boot_a`），按**最后一个** `:` 拆分键值，值去空白。
- 跳过 `all:`、`Finished.`、空行。

#### `list_partitions(serial)`

从 vars 推导：`partition-size:<name>`（十六进制）、`partition-type:<name>`、`is-logical:<name>`。名称以 `_a` / `_b` 结尾的合并为一项（`slotted=True`，size 取 `_a`）。按名称排序。

#### 写操作

| 方法 | 命令 | 说明 |
| --- | --- | --- |
| `flash(part, img, slot)` | `fastboot -s S flash [--slot=a\|b\|all] <part> <img>` | slot=None 不加参数。进度：没有百分比，按阶段估算——`Sending` 阶段 0–70%，`Writing` 阶段 70–100%；sparse 镜像输出 `Sending sparse 'super' 3/9`，用 `(\d+)/(\d+)` 细化。输出含 `FAILED` → `CommandFailed`，把 `FAILED (remote: '…')` 里的原因放进 message。 |
| `erase(part)` | `fastboot erase <part>` | `FAILED (remote: 'Erase is not allowed')` 之类 → `PermissionDenied` |
| `set_active_slot(slot)` | `fastboot --set-active=<a\|b>` | |
| `boot_image(img)` | `fastboot boot <img>` | 进度同 flash 的 Sending 阶段 |
| `flashing_lock(unlock)` | `fastboot flashing unlock` / `flashing lock` | 命令会等待用户在设备上确认，超时设 120 秒；`FAILED (remote: 'Flashing Unlock is not allowed')` → `PermissionDenied("请先在开发者选项中打开“OEM 解锁”")` |
| `sideload(zip)` | `adb -s S sideload <zip>` | 设备需处于 `sideload` 状态，否则 `CommandFailed`。进度从 `serving: '…'  (~45%)` 解析（`\(~(\d+)%\)`，以 `\r` 分隔）。`Total xfer: 1.00x` 表示完成。 |

---

## 3. 共享辅助逻辑（建议）

- `_adb(serial, *args, timeout=30) -> (code, out, err)`：拼 `[adb, "-s", serial, *args]`，统一错误映射（第 4 节），`FileNotFoundError` → `ToolNotFound`，`TimeoutExpired` → `CommandTimeout`。
- `_shell(serial, script, timeout) -> str`：`_adb(serial, "shell", script)`，退出码非 0 时按输出映射错误。
- `_sections(text) -> dict[str, str]`：按 `==NAME` 标记切分多段输出。
- `_Stream` 基类：持有 `Popen`、读线程、停止锁，实现 `stop()` / `running` / “stop 后不再回调”，logcat / perf / shell / screenrecord / scrcpy 复用。
- 长任务统一用一个 `_run_cancellable(args, on_line, cancel)`：逐块读输出（`\r` 和 `\n` 都当作行分隔），每 100 ms 检查 cancel。

## 4. 错误映射

| 输出 / 情况 | 抛出 |
| --- | --- |
| 找不到 adb / fastboot 可执行文件 | `ToolNotFound("找不到 adb：请安装 Android SDK Platform-Tools，或在设置中指定路径")` |
| `error: device unauthorized` | `DeviceUnauthorized("设备未授权：请在手机上允许 USB 调试")` |
| `error: device '<S>' not found`、`no devices/emulators found` | `DeviceNotFound("设备 <S> 未连接")` |
| `error: device offline` | `DeviceNotFound("设备 <S> 离线")` |
| `error: closed`、`device still authorizing` | `DeviceNotFound("设备连接中断，请稍后重试")` |
| 超时 | `CommandTimeout("命令超时（N 秒）：<命令摘要>")` |
| `Permission denied`、`Operation not permitted`、`Read-only file system` | `PermissionDenied(...)` |
| 用户取消 | `Cancelled("已取消")` |
| 其它非零退出 | `CommandFailed(<中文概述>, detail=<stderr/stdout 最后几行>, exit_code=N)` |

`detail` 只放原始输出的最后 20 行以内，界面会取最后一行展示。

## 5. 性能与稳健性要求

- `list_devices` 在 2 台在线设备时 < 1 秒（补全信息有缓存）；单台设备无响应不影响其他设备。
- Logcat 5000 行/秒持续 1 分钟，界面不卡、内存稳定（批量回调）。
- 所有 `start_*` 句柄在设备拔掉后 3 秒内调用 `on_exit`。
- 应用退出时界面会对所有活动页调用 unload → `stop()`；`stop()` 要保证子进程被结束（必要时 `kill`），不留下孤儿 adb 进程（screenrecord 例外：先 SIGINT 再拉取）。
- 不要修改设备的全局设置（`show_touches` 之类用完必须恢复）。
