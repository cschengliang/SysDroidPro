"""功能启动页：Winscope 真实启动 / 停止（本机进程，不需要设备；使用空闲端口，不影响正在运行的实例）。"""
from __future__ import annotations

import socket
import urllib.request

import pytest

from app.backend.mock import MockBackend
from app.core.host_services import port_open
from tests.conftest import pump, wait_until

pytestmark = pytest.mark.ui


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def page(qapp, tmp_path):
    from app.core.commands import CommandStore
    from app.core.settings import Settings
    from app.core.state import AppState
    from app.main_window import MainWindow
    s = Settings(tmp_path / "s.ini")
    s.set("auto_root", False)
    s.set("last_page", "launcher")
    s.set("winscope_web_port", _free_port())
    s.set("winscope_proxy_port", _free_port())
    w = MainWindow(AppState(MockBackend(), s, CommandStore(tmp_path / "c.json")))
    w.show()
    pump(qapp, 0.3)
    yield w.pages["launcher"]
    w.close()          # closeEvent 会停止仍在运行的服务
    pump(qapp, 0.2)


def test_winscope_start_serve_and_stop(page, qapp):
    svc = page.services[0]
    assert svc.check_available() is None, "附带的 Winscope 文件应存在"
    assert page.start_btn.isEnabled() and not page.stop_btn.isEnabled()
    page.start_btn.click()
    assert wait_until(qapp, lambda: svc.state == "running", 30), f"state={svc.state} error={svc.error}"
    assert page.stop_btn.isEnabled() and page.open_btn.isEnabled()
    html = urllib.request.urlopen(svc.address(), timeout=5).read().decode("utf-8", "replace")
    assert "<html" in html.lower()
    assert wait_until(qapp, lambda: bool(svc.read_token()), 5), "ADB 代理应输出令牌"
    assert "running" in page.table.table_model.data(page.table.table_model.index(0, 1))
    page.stop_btn.click()
    assert svc.state == "stopped"
    assert wait_until(qapp, lambda: not port_open(svc.web_port) and not port_open(svc.proxy_port), 5)


def test_port_in_use_reports_error(page, qapp):
    svc = page.services[0]
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", svc.web_port))
    blocker.listen(1)
    try:
        page.start_btn.click()
        pump(qapp, 0.3)
        assert svc.state == "error" and "端口已被占用" in svc.error
        assert page.start_btn.isEnabled()
    finally:
        blocker.close()


def test_close_window_stops_service(qapp, tmp_path):
    from app.core.commands import CommandStore
    from app.core.settings import Settings
    from app.core.state import AppState
    from app.main_window import MainWindow
    s = Settings(tmp_path / "s.ini")
    s.set("auto_root", False)
    s.set("winscope_web_port", _free_port())
    s.set("winscope_proxy_port", _free_port())
    w = MainWindow(AppState(MockBackend(), s, CommandStore(tmp_path / "c.json")))
    w.show()
    svc = w.pages["launcher"].services[0]
    svc.start()
    assert wait_until(qapp, lambda: svc.state == "running", 30)
    w.close()
    assert svc.state == "stopped"
    assert wait_until(qapp, lambda: not port_open(svc.web_port), 5), "关闭主窗口后不应留下进程"
