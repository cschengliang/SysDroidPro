# 交接说明：实现后端与测试（v3）

> 把这份文件整体交给负责后端的模型 / 工程师作为任务说明。

## 背景

Windows 桌面工具 **Android 系统工程师工具箱**（嵌入式 Python 3.14 + PySide6）。使用者是 Android 系统开发工程师，设备是 userdebug，主要用来查询系统模块 / 组件信息。
前端已全部完成并提交（git 仓库根目录是 `rebuild-droidtooxbox/`，前端在 `droidtoolbox/`），目前跑在演示数据（`app/backend/mock.py`）上。
你的任务是实现**真实后端** `app/backend/adb.py` 并补齐**测试**。

## 先读这些文件（按顺序）

1. `app/backend/api.py` —— 接口契约（`Backend` 抽象类、错误类型、流句柄）。**唯一的约定，签名不要改。**
2. `app/backend/models.py` —— 所有返回值的数据结构。
3. `docs/BACKEND_SPEC.md` —— **第 0 节是 v3 的增删改，优先于后文**；后文是逐方法的命令、解析、坑与错误映射。
4. `app/backend/mock.py` + `mock_data.py` —— 行为参考（`_Thread.emit` 的“stop 后不回调”写法、错误类型用法、root / verity / remount 状态机）。
5. `docs/TEST_PLAN.md` —— 第 0 节为 v3 补充；需要写哪些测试、验收标准。
6. `tests/test_contract.py` —— 现有契约测试，你的实现必须通过。
7. `../我的需求.txt` —— 需求 v3（了解每个功能为什么存在）。

## 环境

| 项目 | 位置 / 说明 |
| --- | --- |
| Python | `..\python-3.14.8-embed-amd64\python.exe`（嵌入式；pytest、pytest-timeout、pyflakes 已装）。在 `droidtoolbox` 目录下运行 |
| adb / fastboot | `tool\platform-tools\`（adb 37.0.1）——后端工具查找要支持这个附带目录，见 BACKEND_SPEC 0.2 |
| scrcpy | `tool\scrcpy-win64-v4.1\scrcpy.exe`（4.1，自带另一个 adb，启动时设置环境变量 `ADB`） |
| 测试设备 | 模拟器 `emulator-5554`：Android 16 / API 36，userdebug，x86_64，SELinux Enforcing，只有 user 0，adbd 默认非 root |

```bat
cd droidtoolbox
..\python-3.14.8-embed-amd64\python.exe -m pytest                                   :: 全部
set DTB_DEVICE=emulator-5554 && ..\python-3.14.8-embed-amd64\python.exe -m pytest -m device
..\python-3.14.8-embed-amd64\python.exe -m pyflakes app tests
..\python-3.14.8-embed-amd64\python.exe main.py                                     :: 有 adb.py 时用真实后端
```

## 交付物

| 文件 | 内容 |
| --- | --- |
| `app/backend/adb.py` | `class AdbBackend(Backend)`，`name = "adb"`，`__init__(self, runner=None)`，实现全部抽象方法 |
| `app/backend/_proc.py` | 子进程 runner（`run` / `popen`，Windows 下 `CREATE_NO_WINDOW`）、流基类 |
| `app/backend/parsers.py` | 纯函数解析器（无 I/O），每个都有单测 |
| `tests/fakes.py` | `FakeRunner`（按命令返回样本、可控的假进程） |
| `tests/fixtures/` | 从模拟器采集的真实输出（`.emu36`），拿不到的手写并标 `.synthetic` |
| `tests/backend/test_parsers.py`、`tests/backend/test_adb.py` | 解析器与假 runner 行为测试 |
| `tests/device/` | 真机测试（`device` / `destructive` 标记），针对 `emulator-5554` |
| `tests/test_contract.py` | `_backends()` 加入 `AdbBackend(runner=FakeRunner...)`，有 `DTB_DEVICE` 时加入真实实例 |
| `docs/BACKEND_REPORT.md` | 完成报告：已实现的方法、与规格不同之处（附原因）、模拟器上的实测发现（命令输出格式差异等）、测试结果、遗留问题、发现的界面问题 |

## 硬性约束

- 后端**只用标准库**，不得导入 PySide6 / PyQt6 / tbui（有测试检查）。后端运行时不新增第三方依赖；测试依赖可以用 pip 装进嵌入式 Python。
- 方法签名、参数名与 `api.Backend` 完全一致（有测试检查）；界面按关键字调用。
- 所有 adb 命令带 `-s <serial>`；设备端路径与参数用 `shlex.quote`。
- `stop()` 返回后不再触发数据回调；所有流在设备断开后调用 `on_exit(错误)`。
- 错误信息是中文、给最终用户看；原始输出放 `detail`。拿不到的字段用 `None`，拿不到的进度 `percent=None`，不要编造。
- **不要修改** `app/ui/`、`app/pages/`、`app/main_window.py`、`app/core/`、`android-toolbox-qt-style/`、`app/backend/api.py`、`app/backend/models.py`。
  发现界面 bug 或契约需要调整时，写进 `docs/BACKEND_REPORT.md`，不要顺手改。
- 在模拟器上**禁止**：`reboot`、`disable-verity`、留下未恢复的 remount、刷写 / 擦除分区、`pm clear` 系统应用、停用系统组件或系统应用。
  其它修改（setprop debug.*、settings put、启停非核心 init 服务等）测试结束后必须恢复原值。
- 不要修改 `tool/`、`../python-3.14.8-embed-amd64/`（除了用 pip 装测试依赖）。

## Git 工作方式

- 在新分支 `backend-adb` 上工作（从 `main` 创建）。
- 按功能分组小步提交（工具查找与 runner、设备与版本信息、root / remount、命令与 shell、logcat、属性与 settings、服务 / HAL / 进程、应用与组件、文件、崩溃记录、scrcpy、测试补齐、报告），每次提交前 `pytest` 与 `pyflakes` 通过。
- 提交信息用中文，末尾加一行 `Co-Authored-By: Codex <noreply@openai.com>`。不要 push，不要改写已有提交。

## 建议的工作顺序

1. `_proc.py` + 工具查找 + `tool_info` / `list_devices` / `device_detail` / `root_state`，在模拟器上验证。
2. 只读能力：`shell` / `run_adb` / `get_props` / `get_prop_contexts` / `list_settings` / `list_binder_services` / `dumpsys` / `list_init_services` / `list_hals` / `list_processes` / `process_detail` / `list_users` / `list_packages` / `package_detail` / `package_components` / `dump_package` / `list_apex_modules` / `list_dir` / `screenshot` / `list_crash_files` / `read_text`。
3. 流：`start_logcat`（含 dmesg）→ `open_shell` → `start_command` → `start_scrcpy`。
4. 写操作：root / unroot、remount、包操作（带 user）、组件启停、settings put / delete、setprop、init 服务启停、push / pull / delete / mkdir / rename、install、bugreport、kill_process、dump_backtrace。
5. 每完成一组就补解析器测试与假 runner 测试；最后补真机测试、契约测试扩展、真实后端界面冒烟。
6. 写 `docs/BACKEND_REPORT.md`。

## 完成标准

- `python -m pytest` 全绿（无设备）；`DTB_DEVICE=emulator-5554 python -m pytest -m device` 全绿（或个别用例有明确原因的 skip）。
- `pyflakes app tests` 无输出。
- `main.py` 连上模拟器后状态栏没有 MOCK 标记，12 个页面都能打开且无未处理异常。
- `docs/BACKEND_REPORT.md` 完整。
