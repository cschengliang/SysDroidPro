# 交接说明：实现后端与测试

> **注意（2026-10-18）**：前端已按 我的需求.txt v3 调整，接口契约 `app/backend/api.py` / `models.py` 已同步更新（新增 root_state、remount 流程、settings、服务 / HAL / 进程、多用户、组件、APEX、run_adb 等，去掉了性能、录屏、fastboot 刷机相关方法）。本文档尚未同步，待确认实施后更新；在此之前以 `api.py` 为准。


> 把这份文件整体交给负责后端的模型 / 工程师作为任务说明。

## 背景

这是一个 Windows 桌面工具：**Android 系统工程师工具箱**（Python 3.14 嵌入式 + PySide6）。界面已全部完成，目前跑在演示数据（`app/backend/mock.py`）上。你的任务是实现**真实后端**并补齐**测试**，界面代码不需要改。

## 先读这些文件（按顺序）

1. `app/backend/api.py` —— 接口契约（`Backend` 抽象类、错误类型、流句柄）。**这是唯一的约定，签名不要改。**
2. `app/backend/models.py` —— 所有返回值的数据结构。
3. `docs/BACKEND_SPEC.md` —— 每个方法用什么命令、怎么解析、有哪些坑、错误怎么映射。
4. `app/backend/mock.py` —— 行为参考（尤其 `_Thread.emit` 的“stop 后不回调”写法、错误类型的用法）。
5. `docs/TEST_PLAN.md` —— 需要写哪些测试、验收标准。
6. `tests/test_contract.py` —— 现有契约测试，你的实现必须通过。

## 你要交付的

| 文件 | 内容 |
| --- | --- |
| `app/backend/adb.py` | `class AdbBackend(Backend)`，`name = "adb"`，`__init__(self, runner=None)`。实现全部抽象方法。 |
| `app/backend/_proc.py` | 子进程 runner（`run` / `popen`，Windows 下 `CREATE_NO_WINDOW`），流基类。 |
| `app/backend/parsers.py` | 纯函数解析器（无 I/O），每个都有单测。 |
| `tests/fakes.py` | `FakeRunner`（按命令返回样本输出、可控的假进程）。 |
| `tests/fixtures/*.txt` | 真实设备输出样本（清单见 TEST_PLAN 第 1 节）。没有真机时先按 BACKEND_SPEC 中的样例手写，并在文件名里标注 `.synthetic`，之后用真机样本替换。 |
| `tests/backend/test_parsers.py`、`tests/backend/test_adb.py` | 见 TEST_PLAN 第 1、2 节。 |
| `tests/device/test_device.py` | 真机测试（`device` / `destructive` 标记）。 |
| `tests/test_contract.py` | 在 `_backends()` 里加入 `AdbBackend(runner=FakeRunner...)`。 |

## 硬性约束

- 后端**只用标准库**，不得导入 PySide6 / PyQt6 / tbui（`test_backend_package_does_not_import_qt` 会检查）。
- 方法签名、参数名与 `api.Backend` 完全一致（`test_signatures_match_contract` 会检查）；界面用关键字参数调用。
- 所有 adb 命令带 `-s <serial>`；设备端路径用 `shlex.quote`。
- `stop()` 返回后不再触发数据回调；所有流在设备断开后调用 `on_exit(错误)`。
- 错误信息是中文、给最终用户看的；原始输出放 `detail`。
- 拿不到的字段用 `None`，拿不到的进度用 `percent=None`，不要编造。
- 自动化测试里**禁止**真机执行 flash / erase / flashing lock|unlock / reboot / set_active_slot。
- 不改 `app/ui`、`app/pages`、`app/main_window.py`、`android-toolbox-qt-style/`。如果发现界面 bug 或契约需要调整，单独列出来说明，不要顺手改。

## 运行环境

```bat
:: 在 droidtoolbox 目录下
..\python-3.14.8-embed-amd64\python.exe main.py            :: 启动（有 adb.py 时自动用真实后端）
..\python-3.14.8-embed-amd64\python.exe main.py --mock     :: 强制演示数据
..\python-3.14.8-embed-amd64\python.exe -m pytest          :: 测试（pytest 已安装）
..\python-3.14.8-embed-amd64\python.exe -m pip install xxx :: 如需测试依赖（后端运行时不允许新增依赖）
```

本机 adb 位于 `%LOCALAPPDATA%\Android\Sdk\platform-tools\adb.exe`。嵌入式 Python 不会把脚本目录加入 `sys.path`，`main.py` 和 `pytest.ini` 已处理。

## 建议的工作顺序

1. `_proc.py` + 工具查找 + `tool_info` / `list_devices` / `device_detail`，用 `main.py` 启动确认设备总览页正常。
2. `shell` / `get_props` / `list_services` / `dumpsys` / `list_dir` / `list_packages` / `package_detail` / `screenshot`（只读能力）。
3. 流：logcat → open_shell → perf → screenrecord → scrcpy。
4. 写操作：install / uninstall / 包操作 / push / pull / delete / setprop / bugreport。
5. fastboot 与 sideload（只用假 runner 测试）。
6. 每完成一组就补对应的解析器测试和假 runner 测试，最后加真机测试。

## 完成标准

- `python -m pytest` 全绿；`-m device` 在真机上全绿。
- 启动后状态栏没有“演示数据”标记；13 个页面在真机上可用；Logcat 高频日志下不卡。
- 提交说明里列出：已实现的方法、未能实现或行为与规格不同的地方（附原因）、发现的界面问题。
