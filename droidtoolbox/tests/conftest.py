"""公共夹具。Qt 必须在导入前设为 offscreen，所以放在最顶部。"""
from __future__ import annotations

import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if os.name == "nt":  # offscreen 平台没有字体回退，截图里的中文需要系统字体目录
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"))


def pytest_collection_modifyitems(config, items):
    skip_dev = pytest.mark.skip(reason="未设置 DTB_DEVICE")
    skip_des = pytest.mark.skip(reason="未设置 DTB_DESTRUCTIVE=1")
    for it in items:
        if "device" in it.keywords and not os.environ.get("DTB_DEVICE"):
            it.add_marker(skip_dev)
        if "destructive" in it.keywords and os.environ.get("DTB_DESTRUCTIVE") != "1":
            it.add_marker(skip_des)


@pytest.fixture(scope="session")
def qapp():
    from tbui._qt import QtWidgets
    from app.core.settings import DEFAULTS
    from app.ui import style
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    style.install(app, DEFAULTS["theme"])     # 与首次启动一致
    return app


def pump(app, seconds: float):
    """处理事件若干秒（让后台任务的回调回到主线程）。"""
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def wait_until(app, cond, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return cond()
