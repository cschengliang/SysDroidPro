# SysDroidPro（Android 系统工程师工具箱）

面向 Android 系统开发工程师（userdebug 设备）的 Windows 桌面工具，重点是**查询系统各模块、组件的信息**：
设备版本与 Root / Remount、ADB 命令库、Shell、Logcat（含 dmesg 与 SELinux 拒绝过滤）、崩溃记录、系统属性与 settings（快照对比）、
Binder 服务 / init 服务 / HAL / 进程、按 Android 用户的应用与组件、文件管理、scrcpy 投屏。

界面基于同级目录的 `android-toolbox-qt-style`（tbui 样式包），运行在同级目录的嵌入式 Python 3.14 上。

## 从源码运行

仓库不包含嵌入式 Python 和附带工具，首次运行需准备环境：

1. 将 Windows x64 嵌入式 Python 解压到仓库根目录的 `python-3.14.8-embed-amd64/`。在 `python314._pth` 中加入 `Lib\site-packages`，并取消 `import site` 的注释。
2. 在嵌入式 Python 目录安装依赖（最后一行仅用于开发与测试）：

   ```bat
   curl -O https://bootstrap.pypa.io/get-pip.py
   python.exe get-pip.py
   python.exe -m pip install PySide6==6.12.0
   python.exe -m pip install pytest pytest-timeout pyflakes
   ```

3. 可选：在 `droidtoolbox/tool/` 下放入 `platform-tools/`、`scrcpy-win64-v4.1/`、`tools/winscope-android17/` 和 `perfetto-local/`。Perfetto 目录应包含 `ui/` 和 `bin/windows-amd64/trace_processor_shell.exe`。也可以在偏好设置中配置工具路径；adb 还支持从 `PATH` 或 Android SDK 中查找。
4. 在 `droidtoolbox` 目录启动：

   ```bat
   run.bat                 :: 无控制台窗口启动
   run.bat --mock          :: 演示数据（不连接设备）
   run-debug.bat --mock    :: 带控制台，便于看异常
   ```

参数：`--mock` 演示数据；`--dark` / `--light` 指定主题（默认浅色，之后会记住）。快捷键：`Ctrl+T` 切换主题，`Ctrl+B` 收起侧栏，`F5` 刷新当前页，`Ctrl+1…9` 切换页面；ADB 命令页 `Ctrl+Enter` 执行、`Ctrl+S` 保存、`Ctrl+N` 新建。

> 默认连接真实设备（`app/backend/adb.py`）；`--mock` 使用演示数据，状态栏显示 MOCK 标记。

## 构建发布版

```bat
..\python-3.14.8-embed-amd64\python.exe packaging\build_release.py            :: 构建 + 自检 + zip
..\python-3.14.8-embed-amd64\python.exe packaging\build_release.py --no-zip   :: 只生成目录
```

输出 `<仓库>/dist/SysDroidPro-<版本>-win64/` 与同名 `.zip`：免安装绿色版，双击 `SysDroidPro.exe` 启动（.NET Framework 自带的 csc 编译的启动器，调用 `runtime\pythonw.exe`）。运行时只保留 PySide6 的 QtCore / QtGui / QtWidgets / QtSvg，源码预编译为 .pyc，附带 adb、scrcpy、Winscope、本地 Perfetto（不含 .pdb 与原始下载包）。构建完成后会用打包后的运行时无窗口启动并逐页打开做自检。版本号取自 `app/__init__.py`。

## 页面

| 分组 | 页面 | 内容 |
| --- | --- | --- |
| 设备 | 设备总览 | 设备列表；Root / Remount / 重启按钮与状态（Remount 自动先 root，需要重启时询问一次、设备回来后自动继续）；版本信息（各分区指纹不一致时标出）、复制全部；APEX 模块；操作输出 |
| 调试 | ADB 命令 | 命令库（113 条按系统模块分组的内置命令 + 我的命令）、草稿 / 未保存提示、带类型参数（包名 / 服务 / PID / 属性从设备选择，文件 / 目录选择）、需要 root 提示、多行、持续运行可停止、着色结果、执行历史（重启后保留、可重新执行）、复制 adb 命令行、导入导出 JSON |
|  | Shell 终端 | 持久会话；“常用命令”读取命令库 |
|  | Logcat 日志 | 级别 / 文字 / 正则、PID 或包名、SELinux 拒绝、dmesg、暂停、导出 |
|  | 崩溃与 Bug 报告 | tombstone / ANR / dropbox 预览、生成 bugreport |
| 系统 | 系统属性 | getprop、前缀过滤、SELinux 上下文列、监视所选属性、快照对比、setprop |
|  | 系统设置 | settings system / secure / global、按用户、常用开关、快照对比 |
|  | 服务与组件 | Binder 服务（dumpsys）、init 服务（启停）、HAL（lshal / AIDL）、进程（status、线程、.so、调用栈） |
| 管理 | 应用管理 | 自动识别 Android 用户；按用户列包；概览 / 组件（启停）/ 权限 / 原始 dump；批量 dump |
|  | 文件管理 | 浏览、上传下载、新建、重命名、删除 |
|  | 屏幕投射 | scrcpy 独立窗口（标题含设备名）、窗口位置大小记忆、按键面板 |
| 工具箱 | 功能启动 | 每行独立启动 / 停止本机辅助服务：Winscope（Web 界面 + ADB 代理）、本地 Perfetto（UI + 原生 Trace Processor）|
|  | 偏好设置 | 工具路径、主题、连接后自动 adb root、Winscope / Perfetto 端口、导出目录 |

## 目录

```
droidtoolbox/
├─ main.py                  入口（处理 sys.path、主题、后端选择）
├─ run.bat / run-debug.bat  用嵌入式 Python 启动
├─ packaging/               构建绿色版：build_release.py、启动器 launcher.cs
├─ pytest.ini
├─ app/
│  ├─ backend/
│  │  ├─ api.py             ★ 接口契约：Backend 抽象类、错误类型、流句柄
│  │  ├─ models.py          ★ 数据结构（dataclass，无 Qt 依赖）
│  │  ├─ mock.py            演示后端（userdebug 行为：root / verity / remount 状态）
│  │  ├─ mock_data.py       演示用样例数据
│  │  └─ adb.py             真实后端（adb / fastboot / scrcpy）
│  ├─ core/
│  │  ├─ jobs.py            线程池调用、进度、取消；回调转回主线程（run / on_ui）
│  │  ├─ state.py           AppState：设备轮询、当前设备、自动 root、提示；Context：页面依赖
│  │  ├─ commands.py        ADB 命令库（data/commands.json）、参数语法、执行历史（data/command_history.json）
│  │  ├─ command_presets.py 内置命令数据（按系统模块分组）
│  │  └─ settings.py        便携设置 data/settings.ini
│  ├─ ui/
│  │  ├─ common.py          DataTable、TextViewer、TaskStrip、ToastHost、Dialog、SegmentStack …
│  │  ├─ snapshot.py        快照记录与对比（data/snapshots/）
│  │  ├─ style.py / app.qss 在 tbui 主题上叠加的样式
│  ├─ pages/                13 个页面 + base.py（按设备状态切换内容 / 空状态）
│  └─ main_window.py        侧栏、设备切换器、状态栏、快捷键
├─ docs/                    后端历史规格、实测报告与当前测试说明
└─ tests/                   后端、命令库、契约、离屏界面与设备测试
```

## 架构要点

- **界面只依赖 `Backend` 契约。** `Context.run()` 把同步后端调用交给应用自己的 `JobManager`；`Context.start_stream()` 登记 Shell、Logcat、持续命令和 scrcpy。关闭应用时停止轮询、取消队列及可取消任务，并清理应用启动的流和子进程；已经执行的设备操作不保证回滚。
- **页面生命周期**（`pages/base.py`）：`requires` 声明需要的设备状态。当前设备切换、断开或 transport 变化会推进连接代次。设备查询用 `Page.request(key, ...)`，只有本代次同一请求键的最新回调可以更新页面；写入用 `Page.run_device()` 约束回调，提交时传入明确的目标。
- **设备枚举**：ADB 和 Fastboot 分别保留最近一次成功快照；查询未完成或失败时保持已有结果，只有成功返回的新快照才能确认该来源中的设备是否离线。
- **当前设备是全局的**：侧栏顶部的设备切换器、设备总览列表都会切换它，所有页面跟随。
- **危险操作**（停用组件、启停核心 init 服务、修改敏感 settings、清除数据、卸载、删除文件、结束进程、关机）全部二次确认，确认框写明对象。
- **主题监听用绑定方法**：连接 `theme().changed` 时用控件的方法而不是 lambda，控件销毁后 Qt 会自动断开。

## 新增一个页面

1. 在 `app/pages/` 新建文件，继承 `Page`，设置 `id` / `title` / `icon`（tbui/icons 中的名字）/ `requires`，在 `build()` 搭界面，`load()` / `unload()` 处理数据。
2. 在 `app/pages/__init__.py` 的 `GROUPS` 里登记。
3. 需要新的设备能力时，先在 `api.py` 加抽象方法、`models.py` 加数据结构，再同步实现 `mock.py`。

## 测试

```bat
..\python-3.14.8-embed-amd64\python.exe -m pytest
```

离线和真机测试的运行方式见[测试说明](docs/TEST_PLAN.md)。

## 对样式包的改动

开发中对 `android-toolbox-qt-style/tbui` 做了少量补充（向后兼容，原 `demo.py` 仍可运行）：

- `SegmentedControl`：固定尺寸策略（放进工具栏时不再被拉高），新增 `set_value()`。
- `LogFilterProxy`：Qt 6.10+ 使用 `beginFilterChange/endFilterChange`；新增按 PID 过滤（`set_filter(pids=...)`）。
- `Switch`、`DeviceStatus`、`Sidebar`、`DeviceList`、`LogView`：主题监听改为绑定方法，修复控件销毁后切换主题报错。
- `Switch`：最小尺寸等于文字宽度，放进工具栏时文字不被截断。
- `PropertyList`：键名列宽改为 35% 并限定在 110–170px（原为固定 40%）。
- `icons/`：新增 Lucide 图标 chevron-up、arrow-up、arrow-left、house、folder-plus、save、lock、lock-open、layers、server、list、volume-1、volume-2、video、circle、image、pencil、shield、sliders、sun。

## 第三方组件

发布包附带或使用以下组件，版权归各自作者，按各自许可证分发：

- [Qt for Python (PySide6)](https://www.qt.io/qt-for-python)：LGPLv3。
- [Python](https://www.python.org/)：PSF License。
- [Android SDK Platform-Tools](https://developer.android.com/tools/releases/platform-tools)、[Perfetto](https://perfetto.dev/)、Winscope、[scrcpy](https://github.com/Genymobile/scrcpy)：Apache 2.0。
- [Lucide](https://lucide.dev/) 图标：ISC；[JetBrains Mono](https://www.jetbrains.com/lp/mono/) 字体：OFL 1.1。
