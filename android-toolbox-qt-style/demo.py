"""演示窗口：python demo.py [light]

用模拟数据展示 Logcat、设备总览、Shell 三个页面。Ctrl+T 切换深色 / 浅色。
"""
import random
import sys

from tbui._qt import QtCore, QtGui, QtWidgets, Qt
import tbui as ui

W = QtWidgets

DEVICES = [
    dict(model="Pixel 8 Pro", manufacturer="Google", serial="3A281FDH2000XY", android="15", api=35, transport="usb", state="device", battery=82),
    dict(model="14", manufacturer="Xiaomi", serial="192.168.1.23:5555", android="14", api=34, transport="wifi", state="device", battery=64),
    dict(model="Galaxy S23", manufacturer="Samsung", serial="R5CW20ABCDE", android="14", transport="usb", state="unauthorized"),
    dict(model="Pixel 7", manufacturer="Google", serial="28011FDH30016K", transport="usb", state="fastboot"),
    dict(model="sdk_gphone64_x86_64", serial="emulator-5554", android="15", api=35, transport="emulator", state="device", battery=100),
]

SAMPLE = """10-18 13:16:02.481  1532  1588 I ActivityManager: Start proc 23011:com.example.shop/u0a312 for top-activity {com.example.shop/com.example.shop.MainActivity}
10-18 13:16:02.497 23011 23011 D ApplicationLoaders: Returning zygote-cached class loader: /system_ext/framework/androidx.window.extensions.jar
10-18 13:16:02.512 23011 23011 V GraphicsEnvironment: ANGLE Developer option for 'com.example.shop' set to: 'default'
10-18 13:16:02.803 23011 23040 D OkHttp: --> GET https://api.example.com/v2/catalog?page=1
10-18 13:16:03.118 23011 23040 W OkHttp: <-- HTTP FAILED: java.net.SocketTimeoutException: timeout
10-18 13:16:03.120 23011 23011 E AndroidRuntime: FATAL EXCEPTION: main
10-18 13:16:03.120 23011 23011 E AndroidRuntime: java.lang.IllegalStateException: Catalog not loaded
10-18 13:16:03.120 23011 23011 E AndroidRuntime: 	at com.example.shop.catalog.CatalogViewModel.requireItems(CatalogViewModel.kt:88)
10-18 13:16:03.188  1532  1561 I ActivityManager: Process com.example.shop (pid 23011) has died: fg  TOP
10-18 13:16:03.913  1028  1077 F libc    : Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE) in tid 1077 (HwBinder:1028_2)
10-18 13:16:04.877   657   657 D BatteryService: Processing new values: level 82, voltage 4122, temperature 314
10-18 13:16:05.230 23102 23102 I Choreographer: Skipped 34 frames!  The application may be doing too much work on its main thread.""".splitlines()


def hbox(*ws, spacing=8, margins=(0, 0, 0, 0)):
    w = W.QWidget()
    l = W.QHBoxLayout(w)
    l.setContentsMargins(*margins)
    l.setSpacing(spacing)
    for x in ws:
        if x is None:
            l.addStretch(1)
        else:
            l.addWidget(x)
    return w


class LogcatPage(W.QWidget):
    def __init__(self, status: W.QLabel):
        super().__init__()
        self.status = status
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        tabs = W.QTabBar()
        tabs.setDocumentMode(True)
        tabs.setTabsClosable(True)
        tabs.setExpanding(False)
        tabs.addTab(ui.theme().icon("scroll-text", "ink-muted"), "Pixel 8 Pro · main")
        tabs.addTab(ui.theme().icon("scroll-text", "ink-muted"), "Xiaomi 14 · crash")
        tabs.tabCloseRequested.connect(tabs.removeTab)
        lay.addWidget(tabs)

        tb = ui.Toolbar()
        self.pause = tb.add(ui.icon_button("pause", "暂停抓取", checkable=True))
        clear = tb.add(ui.icon_button("eraser", "清空日志"))
        tb.separator()
        self.filter = tb.add(ui.FilterField(width=360))
        self.level = tb.add(ui.SegmentedControl([(x, x) for x in "VDIWE"], "V", mono=True, label="最低日志级别"))
        tb.separator()
        self.follow = tb.add(ui.icon_button("arrow-down-to-line", "跟随最新", checkable=True))
        self.follow.setChecked(True)
        tb.stretch()
        tb.add(ui.select([("main", "缓冲区：main"), ("system", "缓冲区：system"), ("crash", "缓冲区：crash"), ("all", "缓冲区：全部")], size="sm"))
        tb.add(ui.button("导出", size="sm", icon="download"))
        lay.addWidget(tb)

        self.view = ui.LogView()
        lay.addWidget(self.view, 1)

        self.filter.textChanged.connect(self.apply_filter)
        self.filter.optionsChanged.connect(self.apply_filter)
        self.level.changed.connect(self.apply_filter)
        self.view.proxy.invalidPattern.connect(self.filter.set_invalid)
        self.follow.toggled.connect(self.view.set_follow)
        self.view.followChanged.connect(self.follow.setChecked)
        clear.clicked.connect(self.view.log_model.clear)
        self.view.log_model.rowsInserted.connect(self.update_count)
        self.view.log_model.modelReset.connect(self.update_count)

        self.view.log_model.append_rows([ui.LogRow.parse(l) for l in SAMPLE])
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(350)

    def tick(self):
        if self.pause.isChecked():
            return
        row = ui.LogRow.parse(random.choice(SAMPLE))
        now = QtCore.QTime.currentTime().toString("HH:mm:ss.zzz")
        row.time = QtCore.QDate.currentDate().toString("MM-dd") + " " + now
        self.view.log_model.append_rows([row])

    def apply_filter(self, *_):
        o = self.filter.options()
        self.view.set_filter(text=self.filter.text(), min_level=self.level.value(), regex=o["regex"],
                             match_case=o["matchCase"], whole_word=o["wholeWord"])
        self.update_count()

    def update_count(self, *_):
        shown, total = self.view.proxy.rowCount(), self.view.log_model.rowCount()
        self.filter.set_count(shown, total)
        self.status.setText(f"已显示 {shown:,} / {total:,} 行")


class DevicesPage(W.QWidget):
    def __init__(self):
        super().__init__()
        lay = W.QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        left = W.QWidget()
        left.setProperty("surface", "panel")
        left.setFixedWidth(300)
        ll = W.QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        ll.setSpacing(0)
        title = W.QLabel(f"设备  {len(DEVICES)}")
        ui.repolish(title, role="subtitle")
        head = hbox(title, None, ui.icon_button("refresh-cw", "刷新设备"), ui.button("无线配对", size="sm", icon="wifi"),
                    spacing=8, margins=(16, 0, 8, 0))
        head.setFixedHeight(40)
        ll.addWidget(head)
        line = W.QFrame()
        ui.repolish(line, hline=True)
        ll.addWidget(line)
        self.list = ui.DeviceList()
        self.list.set_devices(DEVICES)
        ll.addWidget(self.list, 1)
        lay.addWidget(left)
        vline = W.QFrame()
        ui.repolish(vline, vline=True)
        lay.addWidget(vline)

        scroll = W.QScrollArea()
        scroll.setWidgetResizable(True)
        self.detail = W.QWidget()
        self.detail.setProperty("page", True)
        scroll.setWidget(self.detail)
        lay.addWidget(scroll, 1)
        self.list.deviceSelected.connect(self.show_device)
        self.show_device(DEVICES[0]["serial"])

    def show_device(self, serial):
        d = next(x for x in DEVICES if x["serial"] == serial)
        old = self.detail.layout()
        if old is not None:
            W.QWidget().setLayout(old)  # 丢弃旧布局
        v = W.QVBoxLayout(self.detail)
        v.setContentsMargins(24, 24, 24, 24)
        v.setSpacing(16)

        name = W.QLabel(f"{d.get('manufacturer', '')} {d['model']}".strip())
        name.setFont(ui.theme().display_font(24))
        sub = hbox(ui.DeviceStatus(d["state"]), self._mono(d["serial"]),
                   ui.badge(f"API {d['api']}", mono=True) if d.get("api") else None, None)
        restart = ui.menu([("system", "重启系统", "rotate-ccw"), ("recovery", "重启到 Recovery", "hard-drive"),
                           ("bootloader", "重启到 Bootloader", "zap", "Ctrl+Shift+B"), None,
                           ("off", "关机", "power", None, True)], self)
        actions = hbox(ui.button("截屏", icon="camera"), ui.button("投屏", icon="cast"), ui.button("Shell", icon="terminal"),
                       ui.button("重启", icon="power", menu=restart), ui.button("安装 APK", "primary", icon="download"))
        title_col = W.QVBoxLayout()
        title_col.setSpacing(4)
        title_col.addWidget(name)
        title_col.addWidget(sub)
        top = W.QHBoxLayout()
        top.addLayout(title_col, 1)
        top.addWidget(actions, 0, Qt.AlignmentFlag.AlignTop)
        v.addLayout(top)

        if d["state"] == "unauthorized":
            v.addWidget(ui.Banner("warn", f"{d['model']} 未授权", "请在手机上勾选“一律允许使用这台计算机进行调试”，然后点“允许”。",
                                  action=ui.button("重新检测", size="sm", icon="refresh-cw")))

        props = ui.PropertyList([("型号", d["model"]), ("序列号", d["serial"]),
                                 ("Android 版本", f"{d['android']}（API {d['api']}）" if d.get("api") else "—"),
                                 ("安全补丁", "2026-09-05"), ("内核", "6.1.99-android14-11-g2d3c4a5b"), ("ABI", "arm64-v8a"),
                                 ("分辨率", "1344 × 2992 · 489 dpi"), ("当前槽位", "_a")])
        status = W.QWidget()
        sl = W.QVBoxLayout(status)
        sl.setContentsMargins(16, 16, 16, 16)
        sl.setSpacing(20)
        sl.addWidget(ui.ProgressRow("电量", d.get("battery") or 0, "充电中 · 31.4 °C · 4.12 V"))
        sl.addWidget(ui.ProgressRow("存储", 71, "182.4 / 256.0 GB 已用"))
        sl.addWidget(ui.ProgressRow("正在等待设备进入 Fastboot…", None))
        sl.addWidget(ui.Switch("连接时保持屏幕常亮", True))
        danger = ui.button("清除应用数据…", "danger", icon="trash-2")
        danger.clicked.connect(lambda: ui.confirm(self, "清除应用数据？",
                                                  "将删除 <code>com.example.shop</code> 在 " + d["model"] + " 上的全部数据、缓存与登录状态，"
                                                  "等同于 <code>pm clear</code>。此操作无法撤销。", "清除数据", danger=True))
        sl.addWidget(danger, 0, Qt.AlignmentFlag.AlignLeft)
        sl.addStretch(1)
        grid = W.QHBoxLayout()
        grid.setSpacing(16)
        grid.addWidget(ui.card("设备信息", props), 7, Qt.AlignmentFlag.AlignTop)
        grid.addWidget(ui.card("状态", status), 5, Qt.AlignmentFlag.AlignTop)
        v.addLayout(grid)
        v.addStretch(1)

    @staticmethod
    def _mono(text):
        l = W.QLabel(text)
        l.setFont(ui.theme().mono_font(12))
        ui.repolish(l, role="muted")
        return l


class ShellPage(W.QWidget):
    def __init__(self):
        super().__init__()
        lay = W.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.term = ui.Terminal("husky:/ $")
        lay.addWidget(self.term)
        self.term.write("已连接到 Pixel 8 Pro（3A281FDH2000XY）· adb shell", "system")
        for cmd, out, kind in [("getprop ro.build.version.release", "15", "output"),
                               ("rm /system/app/Example.apk", "rm: /system/app/Example.apk: Read-only file system", "error")]:
            self.term.write(cmd, "input")
            self.term.write(out, kind)
        self.term.submitted.connect(lambda c: self.term.write("（演示窗口不会连接设备）", "system"))


class MainWindow(W.QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Android 调试工具箱")
        self.resize(1280, 800)
        self.setMinimumSize(1024, 640)

        central = W.QWidget()
        h = W.QHBoxLayout(central)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(0)

        side_box = W.QWidget()
        side_box.setProperty("surface", "panel")
        sv = W.QVBoxLayout(side_box)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        self.side = ui.Sidebar()
        for group, items in [("设备", [("devices", "设备总览", "smartphone", 3)]),
                             ("调试", [("logcat", "Logcat 日志", "scroll-text"), ("shell", "Shell 终端", "terminal"),
                                      ("perf", "性能监控", "gauge"), ("bugreport", "Bug 报告", "bug")]),
                             ("管理", [("apps", "应用管理", "package"), ("files", "文件管理", "folder"),
                                      ("mirror", "屏幕投射", "cast"), ("capture", "截屏与录屏", "camera")]),
                             ("系统", [("flash", "刷机与分区", "zap")])]:
            self.side.add_group(group)
            for it in items:
                self.side.add_item(*it)
        sv.addWidget(self.side, 1)
        theme_btn = ui.button("切换深浅色", "ghost", icon="settings")
        theme_btn.clicked.connect(ui.theme().toggle)
        foot = hbox(theme_btn, None, margins=(8, 8, 8, 8))
        sv.addWidget(foot)
        h.addWidget(side_box)

        self.stack = W.QStackedWidget()
        h.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        sb = self.statusBar()
        sb.setSizeGripEnabled(False)
        adb = W.QLabel("ADB 服务运行中")
        adb_dot = ui.DeviceStatus("device", compact=True)
        sb.addWidget(hbox(adb_dot, adb, spacing=4, margins=(8, 0, 0, 0)))
        sb.addWidget(W.QLabel("Pixel 8 Pro"))
        self.count = W.QLabel()
        sb.addPermanentWidget(self.count)
        ver = W.QLabel("adb 35.0.2")
        ver.setFont(ui.theme().mono_font(11))
        sb.addPermanentWidget(ver)

        self.pages = {"devices": DevicesPage(), "logcat": LogcatPage(self.count), "shell": ShellPage()}
        for p in self.pages.values():
            self.stack.addWidget(p)
        self.side.currentChanged.connect(self.open_page)
        self.side.select("logcat")

        sc = QtGui.QShortcut(QtGui.QKeySequence("Ctrl+T"), self)
        sc.activated.connect(ui.theme().toggle)

    def open_page(self, id_):
        if id_ not in self.pages:
            label = next(self.side.item(i).text() for i in range(self.side.count()) if self.side.item(i).data(ui.lists.ROLE_ID) == id_)
            self.pages[id_] = ui.EmptyState("layout-grid", label, "这一页还没有做。设计稿见设计系统，组件可直接复用 tbui 里的实现。")
            self.stack.addWidget(self.pages[id_])
        self.stack.setCurrentWidget(self.pages[id_])


def main():
    app = W.QApplication(sys.argv)
    ui.install(app, sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] in ("dark", "light") else "dark")
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
