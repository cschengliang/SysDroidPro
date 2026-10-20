# Android 系统工程师工具箱

面向 Android 系统开发工程师（userdebug 设备）的 Windows 桌面工具，重点是**查询系统各模块、组件的信息**：
设备版本与 Root / Remount、ADB 命令库、Shell、Logcat（含 dmesg 与 SELinux 拒绝过滤）、崩溃记录、系统属性与 settings（快照对比）、
Binder 服务 / init 服务 / HAL / 进程、按 Android 用户的应用与组件、文件管理、scrcpy 投屏。需求见上级目录 `我的需求.txt`（v3）。

界面基于同级目录的 `android-toolbox-qt-style`（tbui 样式包），运行在同级目录的嵌入式 Python 3.14 上。

## 运行

```bat
run.bat                 :: 无控制台窗口启动
run.bat --mock          :: 演示数据（不连接设备）
run-debug.bat --mock    :: 带控制台，便于看异常
```

参数：`--mock` 演示数据；`--dark` / `--light` 指定主题（默认浅色，之后会记住）。快捷键：`Ctrl+T` 切换主题，`Ctrl+B` 收起侧栏，`F5` 刷新当前页，`Ctrl+1…9` 切换页面；ADB 命令页 `Ctrl+Enter` 执行、`Ctrl+S` 保存。

> 当前状态：**前端按 v3 完成，真实后端待实现**。没有 `app/backend/adb.py` 时自动使用演示数据，状态栏显示“演示数据”标记。

## 页面

| 分组 | 页面 | 内容 |
| --- | --- | --- |
| 设备 | 设备总览 | 设备列表；Root / Remount / 重启按钮与状态（Remount 自动先 root，需要重启时询问一次、设备回来后自动继续）；版本信息（各分区指纹不一致时标出）、复制全部；APEX 模块；操作输出 |
| 调试 | ADB 命令 | 命令库（内置预设 + 我的命令）、编辑保存、`{参数}` 占位、多行、持续运行可停止、着色结果、执行历史、导入导出 JSON |
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
├─ pytest.ini
├─ app/
│  ├─ backend/
│  │  ├─ api.py             ★ 接口契约：Backend 抽象类、错误类型、流句柄
│  │  ├─ models.py          ★ 数据结构（dataclass，无 Qt 依赖）
│  │  ├─ mock.py            演示后端（userdebug 行为：root / verity / remount 状态）
│  │  ├─ mock_data.py       演示用样例数据
│  │  └─ adb.py             真实后端（待实现）
│  ├─ core/
│  │  ├─ jobs.py            线程池调用、进度、取消；回调转回主线程（run / on_ui）
│  │  ├─ state.py           AppState：设备轮询、当前设备、自动 root、提示；Context：页面依赖
│  │  ├─ commands.py        ADB 命令库（data/commands.json）、预设、占位符、执行历史
│  │  └─ settings.py        便携设置 data/settings.ini
│  ├─ ui/
│  │  ├─ common.py          DataTable、TextViewer、TaskStrip、ToastHost、Dialog、SegmentStack …
│  │  ├─ snapshot.py        快照记录与对比（data/snapshots/）
│  │  ├─ style.py / app.qss 在 tbui 主题上叠加的样式
│  ├─ pages/                12 个页面 + base.py（按设备状态切换内容 / 空状态）
│  └─ main_window.py        侧栏、设备切换器、状态栏、快捷键
├─ docs/                    后端规格、测试计划、交接说明（待按 v3 同步）
└─ tests/                   契约测试 + 离屏界面测试（38 个，均通过）
```

## 架构要点

- **界面只依赖 `Backend` 契约。** 后端是纯 Python 同步调用；界面用 `jobs.run()` 放进线程池，结果、进度、异常都经 Qt 信号回到主线程。流式能力（logcat、持续运行的命令、shell、scrcpy）用回调 + `StreamHandle.stop()`。
- **页面生命周期**（`pages/base.py`）：`requires` 声明需要的设备状态；不满足时显示带操作的空状态，满足时在页面可见且设备变化时调用 `load(device)`，设备断开 / 切换时调用 `unload()`。
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

## 对样式包的改动

开发中对 `android-toolbox-qt-style/tbui` 做了少量补充（向后兼容，原 `demo.py` 仍可运行）：

- `SegmentedControl`：固定尺寸策略（放进工具栏时不再被拉高），新增 `set_value()`。
- `LogFilterProxy`：Qt 6.10+ 使用 `beginFilterChange/endFilterChange`；新增按 PID 过滤（`set_filter(pids=...)`）。
- `Switch`、`DeviceStatus`、`Sidebar`、`DeviceList`、`LogView`：主题监听改为绑定方法，修复控件销毁后切换主题报错。
- `Switch`：最小尺寸等于文字宽度，放进工具栏时文字不被截断。
- `PropertyList`：键名列宽改为 35% 并限定在 110–170px（原为固定 40%）。
- `icons/`：新增 Lucide 图标 chevron-up、arrow-up、arrow-left、house、folder-plus、save、lock、lock-open、layers、server、list、volume-1、volume-2、video、circle、image、pencil、shield、sliders、sun。
