"""在 tbui 主题之上叠加 app.qss，切换主题时一起重新生成。"""
from __future__ import annotations

from pathlib import Path

import tbui as ui
from tbui.theme import _VAR

HERE = Path(__file__).resolve().parent


def _app_qss(t: ui.Theme) -> str:
    text = (HERE / "app.qss").read_text("utf-8")
    return _VAR.sub(lambda m: t.values[m.group(1)], text)


def install(app, theme_id: str) -> ui.Theme:
    t = ui.install(app, theme_id)

    def apply(*_):
        app.setStyleSheet(t.qss() + "\n" + _app_qss(t))

    apply()
    t.changed.connect(apply)
    return t
