# 日志抓取与屏幕录制后端实现规范（Codex 交接文档）

本文档定义 `日志抓取`（`log_capture`）页面及 `LogCaptureController` 所依赖的 `Backend` 契约、数据模型、`AdbBackend` 实现细则、解析规则与边界情况，供后续在真实 ADB 后端中实现与维护。

> **实现状态**
> - UI、控制器、Mock 和真实 ADB 后端均已接入。真实实现位于 `app/backend/_capture.py`。
> - 抓取前自动清理默认关闭。日志和 MP4 在同目录的临时文件中写入，成功后替换目标文件。
> - 既有实时 Logcat 使用 `Backend.start_logcat`。

---

## 1. 通用约束（与 `docs/BACKEND_SPEC.md` 一致）

1. **零 Qt 依赖**：`app/backend/` 严禁导入 `PySide6` / `Qt`。
2. **仅使用 Python 标准库**：不要引入第三方依赖。
3. **进程执行规范**：
   - 使用已有 `_base.py` / runner 执行外部进程，绝不在 Windows 下弹出控制台窗口（确保传递 `CREATE_NO_WINDOW`）。
   - 拼接远端 `adb shell` 命令参数时统一使用 `shlex.quote`。
   - 长流式命令与短命令遵循项目既有的并发、取消与错误分类（`DeviceNotFound` / `DeviceOffline` / `CommandTimeout` / `CommandFailed`）。
4. **线程安全**：
   - `start_logcat_capture` 与 `start_screenrecord` 的后台线程会在工作线程触发 `on_progress` / `on_exit` 回调，禁止直接触碰任何 UI 对象；UI/控制器通过 Qt 信号桥接回主线程。
   - 对共享句柄/停止标志的读写需线程安全，确保 `handle.stop()` 幂等且多次调用不会抛异常或漏发 `on_exit`。
5. **契约测试兼容性**：
   - 新增方法全部实现并转正为 `@abc.abstractmethod` 后，运行 `pytest -q -m "not device"` 确保 `tests/test_contract.py`（签名匹配、抽象方法检查）全绿。

---

## 2. 数据模型（已在 `app/backend/models.py` 定义）

所有模型均为 `@dataclass(frozen=True)`：

1. **`LogcatBufferInfo`**
   ```python
   @dataclass(frozen=True)
   class LogcatBufferInfo:
       name: str
       size_bytes: int | None
       consumed_bytes: int | None = None
   ```
   - `name`：缓冲区名称，如 `"main"`、`"system"`、`"crash"`、`"events"`、`"radio"`、`"kernel"`。
   - `size_bytes`：当前环形缓冲区容量（字节）；若解析失败或设备未返回则返回 `None`。
   - `consumed_bytes`：当前已占用字节数（可选）；若未返回则返回 `None`。

2. **`LogcatCaptureResult`**
   ```python
   @dataclass(frozen=True)
   class LogcatCaptureResult:
       path: str
       buffers: tuple[str, ...]
       size_bytes: int
   ```
   - `path`：落盘的本地 `.log` 文件路径。
   - `buffers`：实际生效并参与抓取/导出的缓冲区名称元组（例如 `kernel` 被自动降级移除后，应反映真实生效的缓冲区列表，而非盲目回传入参）。
   - `size_bytes`：文件最终字节数（通过实际文件大小确认）。

3. **`DisplayInfo`**
   ```python
   @dataclass(frozen=True)
   class DisplayInfo:
       id: str
       name: str = ""
   ```
   - `id`：物理/虚拟显示器 ID 字符串（如 `"4619827677550801152"`，用于 `--display-id <ID>`；注意显示器 ID 为 64 位无符号整数字符串，**不要**转换成可能导致精度丢失或截断的类型，统一保留字符串）。
   - `name`：可读标签（例如 `"主屏"`、`"内置屏幕 (HDMI)"`、`displayName` 或空字符串）。

4. **`ScreenrecordOptions`**
   ```python
   @dataclass(frozen=True)
   class ScreenrecordOptions:
       bugreport: bool = True
       bit_rate: int = 8_000_000
       display_id: str | None = None
   ```
   - `bugreport`：是否附加 `--bugreport`。
   - `bit_rate`：码率（单位 `bps`，如 `8_000_000`；传入 `> 0` 时附加 `--bit-rate <bps>`）。
   - `display_id`：非空且非空白时附加 `--display-id <ID>`。

5. **`ScreenrecordResult`**
   ```python
   @dataclass(frozen=True)
   class ScreenrecordResult:
       path: str
       size_bytes: int
       display_id: str | None = None
   ```

---

## 3. 契约方法规格（已在 `app/backend/api.py` 定义）

类型别名：
- `CaptureProgressFn = Callable[[int], None]` — 参数为累计已写入本地文件的**总字节数**。
- `CaptureExitFn = Callable[[LogcatCaptureResult | None, BackendError | None], None]`
- `ScreenrecordExitFn = Callable[[ScreenrecordResult | None, BackendError | None], None]`

### 3.1 `get_logcat_buffer_sizes(self, serial: str, buffers: list[LogBuffer] | None = None) -> list[LogcatBufferInfo]`

- **ADB 命令**：
  - 若 `buffers` 非空：对每个缓冲区或合并传递 `-b <buf>`：
    ```text
    adb -s <serial> logcat [-b <b1> -b <b2> ...] -g
    ```
  - 若 `buffers` 为 `None`：传默认常用集合或直接 `adb -s <serial> logcat -g`。
  - 注意：`logcat -g` 在请求无权访问的缓冲区（例如非 root 设备请求 `kernel`）时，可能局部报错或返回部分行，建议按实际兼容策略处理：如果一次性 `-b ... -g` 因 `kernel` 等失败，可回退为逐个查询或忽略不可访问缓冲区报错、尽量返回可读取项（若整个设备 offline/not found 则必须抛 `DeviceOffline` / `DeviceNotFound`）。
- **输出解析规则（放入 `app/backend/parsers.py`）**：
  Android 各版本 / AOSP / 厂商 ROM 的 `logcat -g` 输出格式略有差异，常见示例：
  - `main: ring buffer is 16 MiB (12.3 MiB consumed), max entry is 5120 B, max payload is 4068 B`
  - `system: ring buffer is 256 KiB (128 KiB consumed), max entry is 5120 B, max payload is 4068 B`
  - `crash: ring buffer is 32 MB (0 B consumed), ...`
  - `events: ring buffer is 256 Kb (10 Kb consumed), ...`（部分旧版用 `Kb`/`Mb` 表示 `KiB`/`MiB` 或 `KB`/`MB`；请按二进制习惯 `K/KiB/KB/Kb -> 1024`、`M/MiB/MB/Mb -> 1024 * 1024`、`G/GiB/GB -> 1024**3`、`B -> 1`，因为 `logcat -G 32M` 本质是 `32 * 1024 * 1024` 字节）
  - 也支持浮点数值如 `12.3 MiB`、`1.5 MB`。
  - 使用正则匹配每行开头的 `<name>: ring buffer is <num>\s*([KMGT]?i?[Bb])` 与可选的 `\(<num>\s*([KMGT]?i?[Bb])\s+consumed\)`，忽略大小写。
- **返回值**：尽量保持与请求 `buffers` 顺序一致（去重）。

### 3.2 `set_logcat_buffer_size(self, serial: str, size_mb: int, buffers: list[LogBuffer] | None = None) -> list[LogcatBufferInfo]`

- **校验**：`size_mb <= 0` 时抛出 `CommandFailed("缓冲区大小必须大于 0 MB")`。
- **ADB 命令**：
  ```text
  adb -s <serial> logcat [-b <b1> -b <b2> ...] -G <size_mb>M
  ```
- **重要细节（内核 / 厂商上限钳制与非 root 降级）**：
  1. 某些内核或系统配置（如 `kernel` pstore/kmsgd 或特定厂商 `logd`）会将单个缓冲区上限钳制在小于请求值（例如请求 `256M`，设备实际只给到 `64M`），而 `logcat -G` 命令本身退出码仍为 `0`。
  2. 部分非 root 设备传入 `-b kernel` 会整体失败；可检测报错后自动剔除 `kernel` 重试（或在提示信息中体现，但不要静默吞掉真实连接错误）。
  3. 设置完后，**必须再执行一次 `get_logcat_buffer_sizes(serial, effective_buffers)`** 并将其作为返回值返回！前端 UI（`LogCapturePage._apply_buffer_infos`）会对比实际返回的 `size_bytes` 与目标值，自动显示如 `当前：128 MB（kernel 实测 64 MB，已受设备上限限制）` 的提示。

### 3.3 `dump_logcat(self, serial: str, buffers: list[LogBuffer], dest_path: str) -> LogcatCaptureResult`

- **用途**：对应工具栏「导出缓存」（一次性抓取当前环形缓冲区已有日志并退出）。
- **前置准备**：
  - 确保 `dest_path` 所在父目录存在（`os.makedirs(os.path.dirname(dest_path), exist_ok=True)`）。
  - 标准化 `buffers`（为空时默认 `["main", "system", "crash"]`）。
- **ADB 命令**：
  ```text
  adb -s <serial> logcat -v threadtime -d -b <b1> [-b <b2> ...]
  ```
- **关键要求**：
  - **直接以二进制流式写入目标文件**（不要把整段几十 MB 日志先解码成大字符串再写回磁盘，防止 OOM 和编码异常）。
  - 优先采用 stdout 重定向至临时文件或流式分块写入 `dest_path`，避免半截坏文件误被当作成功（可先写 `.tmp` 再原子 `os.replace`，或失败时清理不完整文件）。
  - **非 root 降级处理**：若 `buffers` 包含 `"kernel"` 且失败（stderr 提示无法打开 `kernel`/权限拒绝等，且退出码非 0），自动剔除 `"kernel"` 后重试，并在返回的 `LogcatCaptureResult.buffers` 中返回实际生效的缓冲区列表（UI 会据此提示用户实际抓取到的缓冲区）。若剔除后仍为空则抛 `CommandFailed`。
  - **返回值**：返回 `LogcatCaptureResult(path=dest_path, buffers=tuple(effective_buffers), size_bytes=os.path.getsize(dest_path))`。

### 3.4 `start_logcat_capture(self, serial: str, buffers: list[LogBuffer], dest_path: str, on_progress: CaptureProgressFn, on_exit: CaptureExitFn, *, clear_first: bool = False) -> StreamHandle`

- **用途**：持续后台流式抓取 logcat 至本地 `.log` 文件（支持点击「停止抓取」、设备断开时自然结束，或连接 ADB 自动抓取）。
- **前置步骤**：
  1. 若 `clear_first=True`，先执行清理缓冲区（注意：同上，若非 root 且选中含 `kernel`，`logcat -b ... -c` 不应因 `kernel` 报错导致整个抓日志失败；若失败且包含 `kernel`，可先尝试不带 `-b kernel` 清理或降级移除 `kernel`）。
  2. 确保 `dest_path` 父目录存在，以二进制写入模式打开 `dest_path`（带合理缓冲，定期 `flush()`）。
- **ADB 命令**：
  ```text
  adb -s <serial> logcat -v threadtime -b <b1> [-b <b2> ...]
  ```
  - 若设备拒绝 `-b kernel`（进程刚启动即报错退出，且已写入字节数为 0 或极少、stderr 含 `kernel` / `Permission denied` / `failed to open`），应在后台逻辑中自动剔除 `"kernel"` 后立即重连一次流（保证勾选了默认全选的非 root 用户也能正常抓出其余日志），并将真实 `effective_buffers` 记入最终 `LogcatCaptureResult.buffers`。
- **进度回调 `on_progress(total_bytes: int)`**：
  - 每写入一定字节数或节流周期（建议约 `150ms ~ 250ms` 或每 `>= 16 KiB` 节流一次，避免高频触发），调用 `on_progress(total_bytes)`，汇报当前文件累计写入字节数。
  - 注意不要直接向旧的 `LogcatPage.start_logcat` 那样按单行派发给 UI，防止大量日志时 UI 卡顿。
- **停止与退出保证 `on_exit(result, error)`**：
  - `StreamHandle.stop()` 被调用时：主动终止本地 `adb logcat` 子进程（先 `terminate()`，超时 `wait` 后 `kill()`），确保剩余缓冲区 flush 并关闭本地文件句柄。
  - **重要**：无论是用户主动 `handle.stop()`，还是设备拔线导致 `adb logcat` 进程自然退出：
    - 若已经抓取到有效日志文件（哪怕 0 字节但由用户主动停止，或掉线前已写入内容）：构造 `LogcatCaptureResult(path=dest_path, buffers=tuple(effective_buffers), size_bytes=final_size)`，调用 `on_exit(result, None)`，让前端保留「最近一次抓取」路径！
    - 若进程异常失败且未成功完成（如设备启动即拒绝连接、命令参数错误等 `returncode != 0` 且非主动 `stop()`）：
      - 若在**掉线前已有写入数据**（`final_size > 0`），建议依然返回 `LogcatCaptureResult` 并在必要时由断连逻辑处理；若启动即失败且无数据（`final_size == 0`），调用 `on_exit(None, err)`（同时避免留下空垃圾文件或视情况清理）。
    - **必须保证 `on_exit` 有且仅有一次被调用**！前端 `LogCaptureController` 依赖 `on_exit` 清除 `log_recording` / `log_stopping` 状态并释放并发锁。

### 3.5 `list_displays(self, serial: str) -> list[DisplayInfo]`

- **用途**：填充扩展卡片「屏幕录制」中的 `Display ID` 下拉列表，用于 `--display-id <ID>`。
- **ADB 命令**：
  ```text
  adb -s <serial> shell dumpsys SurfaceFlinger --display-id
  ```
- **常见输出格式（Android 10 ~ 16）**：
  ```text
  Display 4619827677550801152 (HWC display 0): port=0 pnpId=QCM displayName="Built-in Display"
  Display 4619827677550801153 (HWC display 1): port=1 pnpId=SEC displayName="HDMI-A-1"
  ```
  - 个别设备可能没有 `displayName="..."` 或名称为空，也可能在括号中仅有 `(HWC display 0)`，或含虚拟显示器。
  - 若 `dumpsys SurfaceFlinger --display-id` 返回空或部分旧设备不支持 `--display-id`，可按需回退解析 `dumpsys display` 中 `mDisplayId=` / `PhysicalDisplayInfo` 或返回空列表 `[]`（UI 支持用户手动输入任意 Display ID）。
- **正则建议（放入 `app/backend/parsers.py`）**：
  - 匹配 `Display\s+(\d+)`，并尝试提取 `displayName="([^"]*)"`（若无则尝试提取 `(HWC display \d+)` 作为辅助 `name`，或留空 `""`）。
  - 去重并保持输出顺序。

### 3.6 `start_screenrecord(self, serial: str, dest_path: str, options: ScreenrecordOptions, on_exit: ScreenrecordExitFn) -> StreamHandle`

- **用途**：启动设备端录屏，并在停止后自动将 MP4 拉取到本地 `dest_path`、清理设备端临时文件。
- **远端临时路径**：
  - 例如 `/data/local/tmp/dtb_screenrecord_<timestamp>_<rand>.mp4`（务必使用独立唯一文件名，防止多开/残留冲突）。
- **构造命令**：
  ```text
  adb -s <serial> shell screenrecord [--bugreport] [--bit-rate <bps>] [--display-id <id>] <remote_path>
  ```
  - `options.bugreport` 为 `True` 时附加 `--bugreport`。
  - `options.bit_rate > 0` 时附加 `--bit-rate {int(options.bit_rate)}`。
  - `options.display_id` 去空白后非空时附加 `--display-id {shlex.quote(did)}`。
  - 注意：`screenrecord` 默认存在设备端 180 秒分段/时长限制（部分新版 Android 支持更大 `--time-limit`，本需求暂不暴露 `--time-limit`，采用默认即可；若到达时长自动结束也属于正常退出，需拉回文件）。
- **优雅停止流程（极为关键！）**：
  - **千万不要**直接杀死本地 `adb shell screenrecord` 进程就结束！那样会导致设备端 `screenrecord` 来不及写入 MP4 `moov` atom 索引，生成无法播放的损坏视频文件！
  - **正确停止方式**：
    1. 启动时或停止时在设备端找到对应 `remote_path` 的 `screenrecord` 进程 PID（例如在 shell 中通过 `echo $$; exec screenrecord ...` 先输出远端 PID 到第一行，或者停止时通过 `pkill -INT -f <remote_filename>` / `pgrep -f <remote_filename>` + `kill -INT <pid>` 发送 `SIGINT` / `kill -2`）。
    2. **推荐最稳妥方案**（无需依赖设备是否内置 `pkill`/`pgrep`）：
       ```sh
       echo __DTB_PID__$$; exec screenrecord [args...] /data/local/tmp/xxx.mp4
       ```
       读取首行 `__DTB_PID__<pid>` 记录远端 PID；当调用 `StreamHandle.stop()` 时，执行一次短命令：
       ```text
       adb -s <serial> shell kill -INT <pid>
       ```
       若设备上某些极老 sh `kill -INT` 不识别信号名，可兼容 `kill -2 <pid>`。
    3. 发送信号后，等待原本的 `adb shell ...` 进程写入 MP4 尾部并**自然退出**（设合理超时如 8~10 秒；若超时才强制终止本地进程并清理远端临时文件）。
- **拉取文件与远端清理（`finally` 中必须清理）**：
  1. 当远端 `screenrecord` 退出后，检查设备端临时文件是否存在且大小 > 0，使用 `adb -s <serial> pull <remote_path> <dest_path>`（确保本地 `dest_path` 父目录存在）。
  2. 无论拉取成功或失败，最终都在 `finally` 中尽力执行 `adb -s <serial> shell rm -f <remote_path>`，不向用户设备残留临时录屏文件（若设备已断开则忽略 rm 失败）。
- **退出回调 `on_exit(result, error)`**：
  - 成功且本地文件已拉取（`os.path.isfile(dest_path)` 且 `size_bytes > 0`）：调用 `on_exit(ScreenrecordResult(path=dest_path, size_bytes=size_bytes, display_id=options.display_id or None), None)`。
  - 若设备中途断线导致无法 `adb pull` 或 `screenrecord` 启动即报错（例如不支持的 `--display-id`、受 DRM `FLAG_SECURE` 限制、Android 版本不支持 `--bugreport` 导致立即退出）：
    - 构造清晰的 `CommandFailed(...)`（附带 stderr 关键信息），调用 `on_exit(None, err)`。
    - 如果本地有残缺的 0 字节或未 pull 完的临时文件，记得清理。
  - **保证 `on_exit` 有且仅有一次被调用**。

---

## 4. `app/backend/api.py` 与现有 `Logcat` 方法的关系

- 现有的 `Backend.start_logcat` 与 `Backend.clear_logcat` 保持不变，供调试页 `LogcatPage` 继续使用。
- `LogCaptureController` 直接复用现有的 `backend.clear_logcat(serial, buffers=...)` 实现「清理缓冲区」。
- 注意检查 `AdbBackend.clear_logcat` 当前实现（位于 `app/backend/_streams.py`）：确认其在传入特定 `buffers`（尤其是包含 `"kernel"` 的非 root 设备）时的兼容性；若需要可顺带做非 root `kernel` 降级保护。

---

## 5. 测试要求

完成 `AdbBackend` 实现后，请按 `docs/TEST_PLAN.md` 补充并运行以下测试：

1. **离线单元测试（不依赖真机，纳入 `pytest -q -m "not device"`）**：
   - `parsers.py` 单元测试：
     - `logcat -g` 各种单位格式（`B`、`KiB`、`MiB`、`KB`、`MB`、`Kb`、`Mb`、浮点、含/不含 `consumed`）。
     - `dumpsys SurfaceFlinger --display-id` 含多个显示器、缺少 `displayName`、空输出的解析与 64 位整数 ID 精度保留。
   - Fake runner / subprocess 单元测试：
     - `set_logcat_buffer_size` 调用 `-G` 后再次调用 `-g` 返回真实钳制后大小。
     - `dump_logcat` / `start_logcat_capture` 对 `kernel` 权限报错时的自动降级，以及流式 `stop()` 的清理与 `on_exit` 单次调用保证。
     - `start_screenrecord` 的 `__DTB_PID__` 提取、`stop()` 时发送 `kill -INT` 等待自然退出后执行 `adb pull` 与 `rm -f` 清理。
   - `tests/test_contract.py` 全部通过（含转正 `@abc.abstractmethod` 后）。
2. **真机集成测试（`@pytest.mark.device`，参考 `docs/TEST_PLAN.md`）**：
   - 使用 `DTB_DEVICE=<serial>` 实测：
     - `get_logcat_buffer_sizes` & `set_logcat_buffer_size(32/128/256)` 实测效果（含钳制情况）。
     - `dump_logcat` & `start_logcat_capture` 持续抓取并校验文件内容非空。
     - `list_displays` 读取真实 `Display ID`。
     - `start_screenrecord` 录制 2~3 秒后停止，验证本地 MP4 可正常打开（含完整 `moov` header/trailer）且设备端 `/data/local/tmp/dtb_screenrecord_*` 已被删除。
3. **静态检查**：
   - 运行 `pyflakes app tests` 无新增告警。
