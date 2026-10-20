"""Android 系统工程师工具箱 — 入口。

    python main.py              # 有 app/backend/adb.py 时连接真实设备，否则使用演示数据
    python main.py --mock       # 强制演示数据
    python main.py --light      # 浅色主题启动（之后记住设置）

嵌入式 Python 的 ._pth 会忽略脚本目录和 PYTHONPATH，所以这里自己把项目目录和 tbui 样式包加进 sys.path。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
STYLE_KIT = Path(os.environ.get("TBUI_PATH", HERE.parent / "android-toolbox-qt-style"))
for p in (HERE, STYLE_KIT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Android 系统工程师工具箱")
    ap.add_argument("--mock", action="store_true", help="使用演示数据，不连接设备")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dark", action="store_true")
    g.add_argument("--light", action="store_true")
    args = ap.parse_args(argv)

    if sys.platform == "win32":  # 任务栏使用自己的图标分组
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DroidToolbox.App")
        except Exception:  # noqa: BLE001
            pass

    from tbui._qt import QtWidgets
    from app.backend import create_backend
    from app.core.settings import Settings
    from app.core.state import AppState
    from app.main_window import MainWindow
    from app.ui import style

    app = QtWidgets.QApplication(sys.argv[:1])
    app.setApplicationName("DroidToolbox")
    app.setApplicationDisplayName("Android 系统工程师工具箱")
    settings = Settings()
    theme_id = "light" if args.light else "dark" if args.dark else settings.get("theme")
    style.install(app, theme_id if theme_id in ("dark", "light") else "dark")
    from app.ui.app_icon import app_icon
    app.setWindowIcon(app_icon())

    state = AppState(create_backend(mock=args.mock), settings)
    win = MainWindow(state)
    win.show()
    state.start()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
