"""主题切换后，输出区里已经写入的着色文字要按新主题重新着色（否则深色模式下浅色主题写的深色字看不清）。"""
from __future__ import annotations

import pytest
import tbui as ui
from tbui._qt import QtGui
from tbui.widgets import TOKEN_PROPERTY

from app.ui.common import TextViewer, output_log

pytestmark = pytest.mark.ui


def _colors(doc: QtGui.QTextDocument) -> dict[str, str]:
    """{颜色令牌: 当前前景色}，覆盖文档里每个带令牌的片段。"""
    out = {}
    block = doc.begin()
    while block.isValid():
        it = block.begin()
        while not it.atEnd():
            f = it.fragment().charFormat()
            token = f.property(TOKEN_PROPERTY)
            if token:
                out[token] = f.foreground().color().name()
            it += 1
        block = block.next()
    return out


@pytest.fixture
def light(qapp):
    ui.theme().set_theme("light")
    yield
    ui.theme().set_theme("light")


def test_terminal_retints_on_theme_change(light):
    log = output_log()
    for kind in ("input", "output", "error", "system"):
        log.write(f"{kind} line", kind)
    before = _colors(log.out.document())
    assert set(before) == {"signal-ink", "ink", "danger", "ink-faint"}
    ui.theme().set_theme("dark")
    after = _colors(log.out.document())
    assert after == {k: ui.theme().color(k).name() for k in before}
    assert after["ink"] != before["ink"]


def test_text_viewer_retints_on_theme_change(light):
    v = TextViewer()
    v.append("$ ls\n", "input")
    v.append("ok\n", "output")
    v.append("boom\n", "error")
    ui.theme().set_theme("dark")
    after = _colors(v.text.document())
    assert after == {k: ui.theme().color(k).name() for k in ("signal-ink", "ink", "danger")}
