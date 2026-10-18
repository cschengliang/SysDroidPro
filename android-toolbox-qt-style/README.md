# Android 调试工具箱 — Qt6 样式包

把设计系统落到 Python + Qt6 上：同一份 `tokens.json` 生成 QSS、调色板、字体和着色图标，Qt 没有的控件（开关、设备卡片、侧边导航、Logcat 行）自绘实现。

## 运行演示

```bash
pip install "PySide6>=6.5"      # 或 pip install "PyQt6>=6.5"，两者都支持
python demo.py                  # 深色
python demo.py light            # 浅色；运行中按 Ctrl+T 切换
```

演示窗口有 Logcat（模拟实时日志、过滤、级别、跟随）、设备总览（设备列表、详情、进度、开关、危险确认框）和 Shell 三页，其余导航项显示空状态。

## 在项目里使用

把 `tbui/` 文件夹复制进项目：

```python
import tbui as ui

app = QApplication(sys.argv)
theme = ui.install(app, "dark")          # Fusion 样式 + 字体 + 调色板 + QSS

save = ui.button("安装 APK", "primary", icon="download")
pause = ui.icon_button("pause", "暂停抓取", checkable=True)
ui.repolish(some_label, role="muted")      # 改动态属性后让 QSS 生效
theme.set_theme("light")                   # 切换主题；用 bind_icon 设的图标会自动重新着色
```

| 文件 | 内容 |
| --- | --- |
| `tbui/tokens.json` | 设计令牌（从设计系统复制，改颜色只改这里） |
| `tbui/style.qss` | QSS 模板，`@令牌名` 为占位符 |
| `tbui/theme.py` | 读取令牌、生成 QSS / QPalette、字体、SVG 图标着色、`repolish`、`bind_icon` |
| `tbui/widgets.py` | Button、IconButton、Menu、Toolbar、SegmentedControl、TextField、FilterField、Select、Switch、DeviceStatus、Badge、Banner、ProgressRow、EmptyState、confirm 对话框、Kbd、Card、PropertyList、Terminal |
| `tbui/lists.py` | Sidebar（分组导航，可折叠）、DeviceList（设备卡片列表） |
| `tbui/logview.py` | LogRow（解析 threadtime 格式）、LogModel（80ms 批量插入、行数上限）、LogFilterProxy（级别 / 文字 / 正则）、LogDelegate、LogView |
| `tbui/fonts/` | JetBrains Mono 400/500/600（TTF，SIL OFL 1.1） |
| `tbui/icons/` | Lucide 图标（ISC） |

## 用 Qt Designer 画界面时

样式靠动态属性选择，在 Designer 的属性面板里给控件加动态属性即可：

- `QPushButton`：`variant` = `primary` / `ghost` / `danger`，`size` = `sm` / `lg`
- `QLabel`：`role` = `muted` / `faint` / `title` / `subtitle` / `caption` / `label`；`badge` = 状态色名；`tone` = 状态色名
- `QFrame`：`toolbar` = true（工具栏）、`card` = true（卡片）、`banner` = 状态色名、`hline` / `vline` = true（分隔线）
- `QLineEdit`：`invalid` = true（错误态）
- `QWidget`：`surface` = `panel`（bg-200）/ `raised`（bg-300）、`page` = true（bg-100）

自绘控件（Switch、Sidebar、DeviceList、LogView、DeviceStatus）在 Designer 里用“提升为…”指向 `tbui`。

## 说明

- 使用系统标题栏。自绘标题栏需要 `FramelessWindowHint` 加 `WM_NCHITTEST` 处理，暂未包含。
- 按钮的焦点策略设为 `TabFocus`：键盘 Tab 时才显示焦点框，鼠标点击后不残留。
- 菜单项无法单独着色，危险项（关机、擦除）用 `danger` 色图标并放在分隔线之后。
- QSS 不支持阴影和过渡：菜单用系统阴影，开关动画用 `QPropertyAnimation` 实现。
