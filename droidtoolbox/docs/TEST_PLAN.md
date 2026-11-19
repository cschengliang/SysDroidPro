# 测试说明

测试入口为 `pytest.ini`，公共夹具与设备测试开关见 `tests/conftest.py`。测试数量和结果以实际执行为准。

## 离线测试

在 `droidtoolbox` 目录执行：

```bat
..\python-3.14.8-embed-amd64\python.exe -m pytest -q -m "not device"
..\python-3.14.8-embed-amd64\python.exe -m pytest -q tests\backend
..\python-3.14.8-embed-amd64\python.exe -m pytest -q tests\core tests\ui
```

- `tests/backend/`：命令执行、错误映射、输出解析、设备、应用权限、文件传输和流式进程。
- `tests/core/`：命令库、参数解析与执行历史。
- `tests/ui/`：Mock 后端下的离屏界面、页面切换、主题、设备状态、命令页和本机服务启动逻辑。
- `tests/test_contract.py`：后端接口契约。
- `tests/fixtures/`：设备采集样本、明确标记的合成样本和独立测试 APK；来源见该目录 README。

Qt 测试默认使用 `offscreen`；测试设置、截图等写入 pytest 临时目录。项目路径由 `pytest.ini` 配置，支持嵌入式 Python。

## 设备测试

指定已连接的测试设备：

```bat
set DTB_DEVICE=emulator-5554
..\python-3.14.8-embed-amd64\python.exe -m pytest -q -m device
```

未设置 `DTB_DEVICE` 时，设备测试自动跳过。修改设备状态的测试还要求显式设置 `DTB_DESTRUCTIVE=1`；仅在专用测试设备上运行，并检查具体测试的恢复逻辑。

`tests/device/` 包含只读查询、真实界面、权限与可恢复修改测试。设备权限、Android 版本和可用工具会影响结果，历史报告中的通过数量不代表当前环境已经验证。

## 辅助脚本与发布自检

- `tests/collect_emu.py`：采集设备输出样本。
- `tests/build_test_apk.py`：构建独立测试 APK。
- `tests/stress_logcat.py`：Logcat 压力测试。
- `packaging/build_release.py`：发布包构建后使用打包运行时进行离屏页面自检。

后端历史实现依据和设备差异记录见 `BACKEND_SPEC.md`、`BACKEND_REPORT.md`；当前接口以 `app/backend/api.py` 和 `models.py` 为准。
