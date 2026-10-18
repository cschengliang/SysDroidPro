# 测试计划

> **注意（2026-10-18）**：前端已按 我的需求.txt v3 调整，接口契约 `app/backend/api.py` / `models.py` 已同步更新（新增 root_state、remount 流程、settings、服务 / HAL / 进程、多用户、组件、APEX、run_adb 等，去掉了性能、录屏、fastboot 刷机相关方法）。本文档尚未同步，待确认实施后更新；在此之前以 `api.py` 为准。


## 运行

```bat
cd droidtoolbox
..\python-3.14.8-embed-amd64\python.exe -m pytest                 :: 全部（无设备时 device 用例自动跳过）
..\python-3.14.8-embed-amd64\python.exe -m pytest tests\backend   :: 只跑后端
..\python-3.14.8-embed-amd64\python.exe -m pytest -m "not ui"     :: 跳过界面测试
set DTB_DEVICE=3A281FDH2000XY && ..\python-3.14.8-embed-amd64\python.exe -m pytest -m device
```

pytest 与 pytest-timeout 已装进嵌入式 Python。`pytest.ini` 已把项目目录和 `../android-toolbox-qt-style` 加入 `sys.path`（嵌入式 Python 不会自动加）。

## 现有测试（基线，必须保持通过）

| 文件 | 内容 |
| --- | --- |
| `tests/test_contract.py` | 后端契约：无遗留抽象方法、签名与 `api.Backend` 一致、不导入 Qt、返回类型、错误类型、流在 `stop()` 后不再回调、长任务进度与取消、Shell 会话。目前参数化只有 `mock`。 |
| `tests/ui/test_pages.py` | 离屏界面：13 个页面都能打开；截屏 / 录屏、安装成功 / 失败 / 取消、Shell、性能采样暂停与离开页面停止、Logcat 暂停 / 过滤 / 错误正则、文件导航与无权限目录、Bug 报告、运行中切换主题、设备重启后保留日志、切换设备清空日志。任何回调里的未处理异常都会让用例失败。 |

当前结果：`28 passed`。

## 需要新增的测试

### 1. 解析器单元测试 `tests/backend/test_parsers.py`（最重要）

`parsers.py` 里每个函数一组用例，输入来自 `tests/fixtures/` 下的**真实设备输出**。请至少从一台真机（最好再加一台不同厂商 / Android 版本）和一台模拟器采集以下样本，文件名带来源，如 `devices_l.pixel8.txt`：

| 样本 | 命令 | 重点用例 |
| --- | --- | --- |
| `devices_l.*` | `adb devices -l` | usb / wifi / mDNS(`_adb-tls-connect`) / emulator / unauthorized / offline；model 下划线；启动服务时的 `* daemon` 行 |
| `fastboot_devices.*` | `fastboot devices` | 空输出；多台 |
| `getprop.*` | `adb shell getprop` | 多行值；空值 `[]`；中文值 |
| `battery.*` | `adb shell dumpsys battery` | 充电 / 放电 / 无线；缺字段 |
| `detail.*` | 2.1 节的分段脚本 | 非 A/B 设备；`wm size` 有 Override；df 缺 /system |
| `proc_stat.*` ×2 | 相隔 1 秒的两次 `/proc/stat` | 差值计算；离线核心 |
| `top.*` | `top -b -n 1 -m 30` | 表头定位；`%CPU` 换算；`RES` 单位 K/M/G |
| `gfxinfo.*` ×2 | `dumpsys gfxinfo <pkg>` 两次 | fps 与 jank 差值；ΔN=0 |
| `thermal.*` | thermal 循环 | 毫度 / 度；异常值过滤 |
| `services.*` | `dumpsys -l` | 排序 |
| `pm_list.*` | `pm list packages -f -U --show-versioncode` | 路径里含 `=` |
| `dumpsys_package.*` | `dumpsys package <pkg>` | 运行时 / 安装时权限；多用户；包不存在 |
| `ls_la.*` | `ls -la /sdcard/`、`/`、`/dev/` | 名称含空格与中文；链接；设备节点 size；Permission denied |
| `dropbox.*` | `dumpsys dropbox` | 条目行；过滤 tag |
| `getvar_all.*` | `fastboot getvar all`（stderr） | 键含冒号；无 `(bootloader)` 前缀（fastbootd）；A/B 合并 |
| `install_fail.*` | 各种 `Failure [...]` | 错误码翻译 |
| `bugreport_progress.*` | `adb bugreport` 输出（含 `\r`） | 百分比解析；最终路径 |
| `sideload_progress.*` | `adb sideload` 输出 | `(~45%)` 解析 |
| `connect.*` | `adb connect` 成功 / 已连接 / 失败 | 退出码为 0 时也能识别失败 |

### 2. AdbBackend 行为测试 `tests/backend/test_adb.py`（假 runner）

在 `tests/fakes.py` 实现 `FakeRunner`：按 `args` 匹配返回预设的 `(code, stdout, stderr)`，记录每次调用；`popen` 返回可控的假进程（可以往 stdout 推数据、模拟退出）。用它验证：

- **命令构造**：每条命令都带 `-s serial`；`install` 选项映射到 `-r -d -g -t`；多个 APK 用 `install-multiple`；`flash` 的 `slot` → `--slot=`；logcat 的 `buffers` / `tail` → `-b` / `-T`；路径经过 `shlex.quote`（含空格、中文、`'`）。
- **错误映射**：第 4 节表格的每一行一条用例（unauthorized、not found、offline、超时、找不到 adb、Permission denied、ro.* setprop、connect 退出码 0 但失败）。
- **取消**：`bugreport` / `push` / `pull` / `flash` / `sideload` 在 cancel 后终止假进程并抛 `Cancelled`；push 取消后删除了远端残留。
- **流**：logcat 攒批（推 2000 行，回调次数远小于 2000，行内容不丢不乱序）；`stop()` 后无回调；假进程意外退出 → `on_exit(DeviceNotFound)`；kernel 缓冲区失败后降级并输出提示行。
- **screenrecord**：`stop()` 发送了 `pkill -INT screenrecord`，之后执行了 pull 与 rm；`show_touches` 被恢复。
- **scrcpy**：参数映射；环境变量 `ADB` 指向 adb 路径；找不到 scrcpy 抛 `ToolNotFound`。
- **list_devices**：一台设备补全信息超时，整体仍在 1 秒内返回，其它设备信息完整；缓存命中时不再执行 getprop。
- **perf**：第一帧 `cpu_total is None`，第二帧按差值计算；`%CPU` 按核心数换算后 ≤ 100。
- **工具查找**：设置路径优先；不存在时退回 PATH / ANDROID_HOME / LOCALAPPDATA（用 `monkeypatch` 改环境变量和 `shutil.which`）。

### 3. 契约测试扩展

在 `tests/test_contract.py::_backends()` 里加入 `AdbBackend(runner=FakeRunner.from_fixtures())`，让同一组契约断言跑在真实实现上；设置了 `DTB_DEVICE` 时再加入不带 runner 的真实实例。

### 4. 真机集成测试 `tests/device/`（`@pytest.mark.device`）

默认只读：`list_devices` 包含 `DTB_DEVICE`；`device_detail` 字段非空率；`get_props` 与 `shell getprop` 一致；`list_dir("/sdcard")`；`screenshot` 是 PNG 且尺寸与 `wm size` 一致；logcat 10 秒内收到行；perf 3 帧。

`@pytest.mark.destructive`（另需 `DTB_DESTRUCTIVE=1`）：安装 / 卸载一个测试 APK（放在 `tests/fixtures/apk/`，自行用 Android Studio 生成最小应用）；`push` → `pull` 往返到 `/data/local/tmp/dtb_test/` 并比对哈希，最后删除；`setprop debug.dtb.test 1` 再读回；录屏 3 秒并检查 mp4 头。

**禁止**在自动化测试里执行 `flash`、`erase`、`flashing_lock`、`reboot`、`set_active_slot`——这些只做假 runner 测试。

### 5. 界面回归

后端完成后，用真机手动走一遍：

1. 设备总览：插拔 USB、无线连接、未授权设备、模拟器、fastboot 设备各自的展示；重启菜单各项。
2. Logcat：高频日志（例如 `adb shell "while true; do log -t DTB spam; done"`）下滚动、过滤、暂停是否流畅；设备重启后旧日志保留。
3. 其余每页的主要操作各做一次，确认错误提示是中文、可理解。
4. 深色 / 浅色各看一遍。

## 验收标准

- `python -m pytest` 全绿（无设备环境）；解析器测试覆盖上表全部样本。
- 有设备时 `-m device` 全绿。
- `main.py` 启动后状态栏不再出现“演示数据”标记，13 个页面的功能在真机上可用。
- `python -m pytest --durations=10` 中没有单个非界面用例超过 5 秒。
