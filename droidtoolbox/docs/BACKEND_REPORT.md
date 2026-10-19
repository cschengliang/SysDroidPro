# 真实 ADB 后端交付报告

日期：2026-10-18 至 2026-10-19（Asia/Shanghai）。基线 `main`：`8c0876f`。

## 交付状态与 Git 位置

`AdbBackend` 已实现 `api.Backend` 的全部 **62 个抽象方法**，`name="adb"`，构造函数为 `__init__(self, runner=None)`。现有工厂自动选择真实后端，保留 `main.py --mock`。运行时只使用 Python 标准库，没有修改 UI、core、api.py、models.py、样式包或 tool 目录。

**环境限制**：原仓库 `.git` 被执行环境明确设为只读。直接 `git switch -c backend-adb main` 被拒绝，错误为无法创建 `.git/index.lock`。本地 clone 又被 Git/MSYS 的 CreateFileMapping 权限错误阻止。因此复制只读 Git 元数据至工作区中可写的 `.backend-adb-git/`，从相同的 `main` 创建 `backend-adb`，继续使用原工作目录开发、测试和分组提交。没有修改原 `.git`，因此原仓库 HEAD 仍为 `main`；新提交和分支位于独立元数据中。

在仓库根目录查看交付分支：

```powershell
git --git-dir=.backend-adb-git --work-tree=. log --oneline main..backend-adb
git --git-dir=.backend-adb-git --work-tree=. status --short
```

最终同时生成根目录的 `backend-adb.bundle`，供解除原 `.git` 只读限制后导入分支：

```powershell
git fetch ./backend-adb.bundle backend-adb:backend-adb
```

工作目录已经包含最终文件，切换原仓库分支前应妥善处理这些现有工作目录改动。没有 push，没有 amend、rebase、reset 或改写已有提交；所有新增提交使用中文信息并带规定的 Co-Authored-By 行。

## 实现清单

| 功能 | 方法 | 实现位置 |
| --- | --- | --- |
| 配置与工具 | configure、tool_info、restart_server | `_base.py`、`_tools.py` |
| 设备与版本 | list_devices、device_detail、list_apex_modules、connect、pair、disconnect、reboot、root_state、root、remount、disable_verity、send_key、screenshot | `_devices.py` |
| 命令与 Shell | shell、run_adb、start_command、open_shell | `_base.py`、`_streams.py` |
| 日志 | start_logcat、clear_logcat，含 dmesg 与 kernel 单独降级 | `_streams.py` |
| 属性与设置 | get_props、get_prop_contexts、set_prop、list_settings、put_setting、delete_setting | `_system.py` |
| 服务与进程 | list_binder_services、dumpsys、list_init_services、control_init_service、list_hals、hal_debug、list_processes、process_detail、dump_backtrace、kill_process、list_users | `_system.py` |
| 应用 | list_packages、package_detail、package_components、set_component_enabled、dump_package、install、uninstall、clear_data、force_stop、launch、set_enabled、set_permission、extract_apk | `_apps.py` |
| 文件 | list_dir、push、pull、delete、mkdir、rename | `_files.py` |
| 崩溃与报告 | list_crash_files、read_text、bugreport | `_crashes.py` |
| 投屏 | start_scrcpy | `_mirror.py` |

`adb.py` 组装上述实现；`_proc.py` 提供 Windows 无控制台的 Runner、增量 UTF-8 双管道读取、受锁保护的回调和异步启动句柄；`_jobs.py` 实现取消与真实/不确定进度；`parsers.py` 中的解析器不执行 I/O。测试使用按命令匹配的 `FakeRunner` 与可控双管道假进程。

设备命令始终指定 `-s serial`。工具版本、devices 枚举、启动/停止 adb server 是全局操作，按 adb 的原生全局命令执行。进入设备 shell 的路径、用户参数、包名、组件名等统一 `shlex.quote`；push/pull 的 sync 协议路径及主机文件路径以独立 argv 传递，不能再加入 shell 引号。用户输入的 shell 脚本原样执行。

## 模拟器实测与实现决定

测试设备为 `emulator-5554`，实际型号为 Android SDK Tablet for x86_64，Android 16 / API 36，build `BP4A.251205.006`，userdebug，SELinux Enforcing。实际工具为 adb **37.0.1**、fastboot **37.0.1-15733141**、scrcpy **4.1**。只有 user 0。

1. **先保存真实输出再实现解析**。首批样本及退出码在 `tests/fixtures/commands.json`，补充采样来源在 `tests/fixtures/README.md`。`.emu36` 为真实 stdout/stderr；不能安全取得的 remount 需重启输出、多用户等使用 `.synthetic`，没有冒充实测数据。没有第二台厂商实机。
2. **HAL 的部分失败可用**。非 root 的 `lshal --neat -itp` 返回 200，同时输出有效的 HIDL 表与警告；不能因退出码丢掉整个表。只在没有解析到数据时退回 `lshal -itp`。`I*/*` 表示可用实现库，不是服务实例，略过；`N/A` 保留为 None。部分 AIDL HAL 的 `dumpsys --pid` 返回 FAILED_TRANSACTION，相应 PID/进程名为 None。
3. **挂载状态按 /system 优先**。本机 `/` 为 ext4 ro，另有 overlay 挂载的 `/system`，同样 ro。不能因为存在 overlay 就判定已可写。verity 初始为 enforcing。root/unroot 后挂载和 verity 均未改变。
4. **Shell 中断必须匹配子进程**。本机 toybox 的 `pkill -INT -P PID` 返回 1，没有中断 sleep；补上匹配表达式 `'.*'` 后可以中断，随后仍可在同一会话执行命令。Shell PID 标记不暴露给界面；无换行和切开的 UTF-8 字符可立即转发。
5. **Android 16 组件 dump 是可见子集**。信息来自 Resolver Table、Registered ContentProviders、Authorities 和所选 User 的启停覆盖列表。去重、补全完整类名并应用该用户覆盖；没有证据的 exported/permission 为 None。不猜测未出现在 dump 中的无 intent-filter 组件种类。停用独立测试包的组件在本机也需要 root，非 root 返回 PermissionDenied。
6. **用户字段真实分段**。Android 16 把 dataDir 和 firstInstallTime 放进 User N 段；不能从整个 dump 取第一个用户的运行时权限。包路径含 `==`，使用最后一个 `=` 拆分。global settings 不带 --user，空串、中文、单引号和 `=` 均做了往返测试。
7. **修复附带 adb 的中文上传行为**。将中文文件 push 到目录时，本机 adb 把远端 basename 截短；原生目录递归上传还出现卡住。显式指定完整远端文件名则正常。上传改为 Python 遍历目录、创建目录、逐文件 adb push 到完整目标；下载也逐文件指定本地目标。中文嵌套目录、空目录及文件 SHA-256 往返通过。较多小文件会比一次原生递归慢。
8. **取消传输保护已有数据**。传输先写私有 `.dtb-upload-*` / `.dtb-download-*` 目录，成功后合入最终位置；取消只删除临时内容。目录合入阶段是同步完成步骤，不承诺多文件原子替换。设备断开时远端清理只能尽力而为；本地临时目录清理会核实绝对路径，拒绝跟随意外链接或越界删除。
9. **DropBox 使用精确毫秒 ID**。普通 `dumpsys dropbox` 只显示秒；补取 `--file` 中的 `tag@毫秒.txt`。读取时按 tag、时间及同秒内顺序匹配 `--print`，条目变化或无法唯一匹配时明确报错。不会误读另一条记录。时间按设备 `date +%z` 转换。普通文本通过 head 限制读取量；/proc 等 stat 大小不可信时显示“原文件大小未知”。
10. **进度不编造**。本机完整 Bugreport 输出没有百分比，只有复制路径和传输摘要；因此采集阶段为 Progress(None, ...)。真实后端完整生成 4,472,707 字节 ZIP，用时 20.958 秒，路径解析及文件存在检查成功。百分比格式另外有 synthetic 测试。
11. **取消 Bugreport 的远端进程**。仅终止本地 adb 会留下本机的 root dumpstate。增加同设备互斥、开始前已有任务检查，并在取消后只给本次新出现且 comm 确为 dumpstate 的 PID 发信号。root 场景实测取消后无残留；不会自动为了取消而 root。非 root 没有结束远端进程的权限时，本地 adb 仍会停止，远端任务可能继续，这是保留的权限限制。
12. **投屏启动不阻塞界面**。设置快照、启动和恢复均在后台执行；返回句柄前只做本地参数构造与工具存在检查。环境变量 ADB 固定指向选定 adb。真实 scrcpy 窗口出现 Texture 输出后可停止；临时设置按原值恢复，缺失键用 delete 恢复。
13. **测试 APK 单独构造**。使用标准库生成无应用代码的 Manifest，再使用本机已有 JBR 签名，不下载 build-tools，不重装原有 com.example.helloworld。独立测试包支持安装、清数据、停用/启用、Receiver 启停、CAMERA 授予/撤销和 APK 导出，最终卸载。

## 测试结果

| 命令（droidtoolbox 目录，嵌入式 Python 3.14.8） | 结果 |
| --- | --- |
| `python -m pytest`，不设置 DTB_DEVICE | **149 passed, 13 skipped** |
| `DTB_DEVICE=emulator-5554 python -m pytest -m device`，默认只读 | **26 passed, 9 skipped, 149 deselected** |
| 同上并设置 `DTB_DESTRUCTIVE=1` | **33 passed, 2 skipped, 149 deselected** |
| `python -m pyflakes app tests` | **退出码 0，无输出** |

默认离线运行跳过 13 个真实设备用例；默认只读设备运行跳过可恢复写操作和相应真实契约用例。启用可恢复写操作后仅有两项明确 skip：真实 root/remount 流程因硬性约束只用 FakeRunner 验证；设备没有预存 debug.dtb.*，属性服务不能删除属性键，因此不创建无法精确恢复“键不存在”状态的新属性。setprop 的构造、空值、写入行为及错误映射已由 FakeRunner 验证，真实只读属性失败类型也已验证。

持续负载检查：`python tests/stress_logcat.py --output ../.test-tmp/stress-logcat.json`，5000 行/秒 × 60 秒。**300,000 行全部按序收到，601 次批回调，单批最大 500 行，耗时 60.09 秒，Python tracemalloc 峰值 185,045 字节。** 这是后端受控管道负载测试，不把它说成真实设备或整套 GUI 的内存测量。

真实界面检查打开全部 12 页并保存截图，另切换 Binder/init/HAL/Process 四个标签，断言无未处理回调异常、无 danger 提示、状态栏无 MOCK。截图在 pytest 临时目录中。已查看设备总览及应用页截图；版本、adbd shell、/system ro、verity on、adb 37.0.1 与真实设备一致。另实际运行 main.py 的 main([]) 入口，使用临时本地偏好设置关闭自动 root，核对真实后端、当前模拟器、12 个页面和无 MOCK 后定时关闭，正常退出。

本机 Windows 沙箱与 Python 3.14 的 0700 临时目录 ACL 不兼容；未处理前原有基线也会报 PermissionError。测试通过显式 `DTB_TEST_INHERIT_ACL=1` 在**测试进程内**令新临时目录继承 ACL，并把 TEMP/TMP 指到工作区 `.test-tmp/`。生产代码、UI、系统 ACL 和嵌入式 Python 安装均未修改。复现环境：

```powershell
cd droidtoolbox
$env:TEMP = 'C:\Users\csche\Documents\rebuild-droidtooxbox\.test-tmp'
$env:TMP = $env:TEMP
$env:DTB_TEST_INHERIT_ACL = '1'
..\python-3.14.8-embed-amd64\python.exe -m pytest
$env:DTB_DEVICE = 'emulator-5554'
..\python-3.14.8-embed-amd64\python.exe -m pytest -m device
$env:DTB_DESTRUCTIVE = '1'
..\python-3.14.8-embed-amd64\python.exe -m pytest -m device
..\python-3.14.8-embed-amd64\python.exe -m pyflakes app tests
```

## 限制与未实测范围

- 没有遗留 NotImplementedError 或未实现的 v3 抽象方法。v3 已删除的性能、录屏、刷机和 sideload 上传接口没有实现。
- 禁止的 reboot、disable-verity、remount、刷写/擦除没有在模拟器上执行；重启/挂载状态机仅 FakeRunner 验证。fastboot、无线 connect/pair 的成功/失败构造与解析有假 runner 测试，没有切换模拟器模式或真实无线配对。
- 组件列表不等同于完整 APK Manifest，部分 exported、permission、PID 等字段仍为 None；没有将缺失信息编造成确定结果。多用户切分、UID/权限和组件覆盖使用 synthetic 用例，本机只有 user 0。
- 目录下载遇到目录符号链接会明确报错，要求单独下载其目标，防止循环或导出整个链接目标树；Windows 无法安全表示的远端文件名也会明确报错。文件夹合入不保证事务原子性，完成阶段的大目录复制不可即时取消。
- 非 root 取消 Bugreport 无法保证终止 root dumpstate；断线时无法保证清除设备端临时文件或立刻恢复 scrcpy 的设备设置。正常连接、具有权限的路径已实测恢复。

## 需要前端配合的问题（未修改受保护代码）

1. **换插设备不会自动选中新设备**：`app/core/state.py::_set_devices` 只有 `_serial is None` 才自动选中。选中过 A 后拔掉 A，再插 B，旧 `_serial` 仍为 A，current 会一直为空。应按“当前选中的 serial 已不在列表”处理，符合需求的“当前无设备自动选中新插入设备”。这是代码审查结论，未做真实拔插。
2. **投屏过早提示已打开**：`app/pages/mirror.py::_start` 在收到异步句柄后立即提示“投屏窗口已打开”。后台还可能在启动或失败，应先显示“正在启动”，收到 Renderer/Texture 或就绪事件再显示成功。窗口字段只记忆手工填写的初始几何参数，没有捕获用户拖动独立 scrcpy 窗口后的新位置/大小；若要求记住实际窗口几何，需要补齐对应的前后端约定。
3. **未授予运行时权限的分类**：规格要求 requested permissions 中没有出现在 install/runtime 段的权限按 runtime=False。独立 APK 初始 CAMERA 正是如此，前端按 runtime 分组后没有授予开关；授予后 dump 才出现该 runtime 项。需要共同确定是否通过设备权限定义补全真实类型，再调整这一规格/显示策略。
4. **组件完整性说明及工具查找提示**：建议组件页说明其数据是 dumpsys 可见子集，避免用户以为没有列出的组件不存在。偏好设置的工具路径提示仍写 PATH/SDK，实际查找优先显式路径、附带工具，再 PATH/SDK；提示需要同步。非空但不存在的显式路径按规格视为未找到，不静默忽略用户配置。

## 设备恢复核对

最后一次设备审计结果：adbd UID **2000**（非 root），build_type=userdebug，**/system 只读**，**verity=enforcing**，traced_probes **running**；无 debug.dtb.* 新键，无 dtb_test* 设置键，无 com.droidtoolbox.backendtest 安装包，无运行中的 dumpstate，无 dtb_probe_* / dtb_sample_* / dtb_test 临时目录。主机无残留 scrcpy 或本次创建的 adb 子进程，仅保留原有 SDK adb server。完整 Bugreport 测试临时 root 后也已恢复。

所有可恢复写用例均使用 finally；没有执行 reboot、remount、disable-verity、刷写/擦除、停用系统组件或清除系统应用数据。独立测试 APK 的组件和数据操作均限定在该测试包内。受保护文件与基线比较没有差异。

## 2026-10-19 后续：运行时权限分类（v3.1）

已按 `BACKEND_SPEC.md` 第 0.3.1 节实现。本节取代上文“需要前端配合的问题”第 3 项关于未授予运行时权限的旧结论：独立测试 APK 的 CAMERA 在只出现在 requested permissions 时，现在返回 `granted=False, runtime=True`，前端可按现有契约显示授予开关，无需修改接口或界面。

### 命令选择与真实样本

先在 `emulator-5554`（Android 16 / API 36）保存原始输出，再实现解析器。命令均带 `adb -s emulator-5554 shell` 前缀，以下四次采样退出码均为 0、stderr 为空；命令及字节数见 `tests/fixtures/permission_definitions_commands.json`。

| shell 命令 | 样本 | 实测结果 |
| --- | --- | --- |
| `dumpsys package permission android.permission.CAMERA android.permission.RECORD_AUDIO android.permission.INTERNET android.permission.WRITE_SECURE_SETTINGS no.such.permission` | `permission_definitions_dumpsys.emu36` | 161,955 字节；能返回已知权限的 `prot=`，但还附带大量包信息，未知权限没有定义。 |
| **`pm list permissions -f -g`** | `permission_definitions_pm.emu36` | **采用此来源**。231,198 字节，解析出 1,281 个定义，其中 143 个含 `dangerous`。CAMERA / RECORD_AUDIO 为 `dangerous|instant`，INTERNET 为 `normal|instant`，WRITE_SECURE_SETTINGS 为 `signature|privileged|development|installer|role`。 |
| `pm list permissions -d -u` | `permission_definitions_pm_d_u.emu36` | 6,078 字节；Dangerous Permissions 段为空，没有 CAMERA / RECORD_AUDIO，不可用于此功能。 |
| `su 2000 sh -c 'pm list permissions -f -g'` | `permission_definitions_pm_shell.emu36` | 本轮核对时 adbd 已是 UID 0；先用 `su 2000 id -u` 确认降到 shell UID，再采样同一查询。定义数量、分类结果与上面完全相同，证明查询不需要 root，未为采样切换 adbd。 |

选择完整定义列表，单次查询同时覆盖该包所有待分类权限及后续包，避免逐个权限启动进程。解析仅使用权限记录的 `protectionLevel:` 和按 `|` 分开的完整 `dangerous` 标志，不依赖权限组、翻译后的标签、描述或内置权限名单。

### 实现与缓存决定

- `app/backend/parsers.py`：新增定义解析和待分类权限提取；仅为 requested 中既无 install 记录、也无所选用户 runtime 记录的权限补全分类。未知定义仍为 `runtime=False`，未授予状态保持 `granted=False`。显式 install 和所选用户 runtime 记录继续覆盖补全值，其他用户的授予状态不会串入。
- `app/backend/_apps.py`：只有存在待分类权限时才请求定义。同一设备首次成功查询整张表，此后跨包、跨用户复用；每台设备单独加锁，并发详情请求只发一次成功查询，慢设备不会阻塞其他设备。
- `app/backend/_base.py`、`_devices.py`：设备消失、离线、未授权、状态变化、ADB `transport_id` 变化、显式 connect/disconnect、root/unroot、重启 adb server 或重新配置工具时使相应缓存失效。即使设备在两轮枚举之间断开又重连、序列号和在线状态相同，也可通过新的 transport ID 失效缓存。失效时不等待正在查询的线程，旧会话返回的结果不会重新写回有效缓存。
- 定义查询超时、非零退出、工具/连接/权限错误或完全无法解析时，详情退回原有分类；不缓存失败，下次详情请求可以重试。包自身查询、包不存在等既有错误处理保持原样。

本次只修改上述后端实现、`tests/fakes.py`、新增的 `tests/backend/test_permissions.py` / `tests/device/test_permissions.py`、四个 `.emu36` 样本及其命令记录/README，并追加本报告。未修改 `api.py`、`models.py`、界面、core、契约测试或 `tool/`。工作留在原工作区；本轮没有 Git 提交、push 或新建独立 Git 目录。并行工程师的界面/core 文件改动保留原状。

### 本轮测试结果

使用嵌入式 Python 3.14.8、既有 `DTB_TEST_INHERIT_ACL=1`，先创建工作区 `.test-tmp/`，并将 TEMP/TMP 指向它。新增权限测试共 **25 项离线测试 + 1 项设备测试**。覆盖真实样本、危险/普通/签名/未知权限、显式 install 和多用户 runtime 优先级、一次批量查询、跨包复用、设备隔离、失败回退及重试、离线/重新上线/transport 更换失效、并发请求和查询中途断开。

| 命令 | 结果 |
| --- | --- |
| `python -m pytest --tb=short`，未设置 DTB_DEVICE | **179 passed, 14 skipped, 2 failed**，133.53 秒；两项失败均在并行新增的 `tests/ui/test_launcher.py`。 |
| `DTB_DEVICE=emulator-5554 python -m pytest -m device --tb=short` | **25 passed, 10 skipped, 1 failed, 181 deselected**，17.71 秒；唯一失败是界面页数断言。 |
| 同上并设置 `DTB_DESTRUCTIVE=1` | **33 passed, 2 skipped, 1 failed, 181 deselected**，28.47 秒；唯一失败同上。新增 CAMERA 设备用例通过。 |
| `python -m pytest -m 'not ui' --tb=short`，未设置 DTB_DEVICE | **159 passed, 13 skipped, 23 deselected**，22.90 秒，全部后端及离线契约通过。 |
| `DTB_DEVICE=emulator-5554 DTB_DESTRUCTIVE=1 python -m pytest tests/device/test_permissions.py -q` | **1 passed**，2.38 秒；独立权限设备用例另行执行通过。 |
| `python -m pyflakes app tests` | **退出码 0，无输出**。 |

按本次任务要求忽略并记录并行界面失败，没有修改相关文件：

1. `tests/ui/test_launcher.py::test_winscope_start_serve_and_stop`：Winscope proxy 进程以退出码 1 结束，服务未进入 running。
2. `tests/ui/test_launcher.py::test_close_window_stops_service`：同样未进入 running。
3. `tests/device/test_ui_real.py::test_twelve_pages_real_backend`：仍要求 12 页，但并行修改已增加到 13 页，默认和 destructive 两次设备运行都在该断言失败。

两个 destructive 跳过项沿用此前条件：禁止在模拟器执行的 root/remount 状态机仅用 FakeRunner 验证，以及设备没有可精确恢复的预存 `debug.dtb.*` 属性。本轮权限功能没有未实现项或规格偏差。

设备权限用例使用已有 `tests/fixtures/apk/backend-test.apk`，拒绝覆盖预装同名包；安装时不带 `-g`，确认 CAMERA 在待分类权限中，再验证 `(runtime, granted)` 依次为 **`(True, False)` → grant → `(True, True)` → revoke → `(True, False)`**。finally 卸载并确认包消失，同时检查 root、挂载和 verity 状态与用例开始时相同。本轮复核实际环境为 adbd UID **0**、`/` ro、`/system` overlay **rw**、verity **enforcing**，与上一轮报告中的设备状态不同；本轮未调用 remount、disable-verity、reboot 或刷写，也未自行改变该环境状态。最终无测试 APK、无 dumpstate、无 `debug.dtb.*` 或 `dtb_test*` 设置残留，traced_probes 为 running。
