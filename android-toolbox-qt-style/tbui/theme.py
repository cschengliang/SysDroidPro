"""主题：读取 tokens.json，生成 QSS、QPalette、字体和着色图标。

用法：
    app = QApplication(sys.argv)
    theme = tbui.install(app, "dark")      # 加载字体、Fusion 样式、调色板和 QSS
    theme.set_theme("light")               # 运行中切换
    btn.setIcon(theme.icon("pause"))       # 或 tbui.bind_icon(btn, "pause")，切换主题时自动重着色
"""
from __future__ import annotations

import json
import math
import re
import tempfile
from pathlib import Path

from ._qt import QtCore, QtGui, QtWidgets, QtSvg, Qt, Signal

HERE = Path(__file__).resolve().parent
_ALIAS = re.compile(r"^\{(.+)\}$")
_VAR = re.compile(r"@([a-z][a-z0-9-]*)")
_RGBA = re.compile(r"rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)")

UI_FAMILIES = ["Segoe UI Variable Text", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"]
DISPLAY_FAMILIES = ["Segoe UI Variable Display", "Segoe UI", "Microsoft YaHei UI", "PingFang SC"]
MONO_FAMILIES = ["JetBrains Mono", "Cascadia Mono", "Consolas", "Microsoft YaHei UI"]

TONES = ["neutral", "signal", "ok", "warn", "danger", "info", "special"]


def qcolor(value: str) -> QtGui.QColor:
    """'#rrggbb' 或 'rgba(r, g, b, a)' → QColor。"""
    m = _RGBA.fullmatch(value.strip())
    if m:
        r, g, b, a = m.groups()
        c = QtGui.QColor(int(float(r)), int(float(g)), int(float(b)))
        c.setAlphaF(float(a) if a is not None else 1.0)
        return c
    return QtGui.QColor(value)


class Theme(QtCore.QObject):
    """当前主题的全部取值。changed 信号在切换主题后发出。"""

    changed = Signal(str)

    def __init__(self, tokens_path: Path | str = HERE / "tokens.json", parent=None):
        super().__init__(parent)
        self.tokens = json.loads(Path(tokens_path).read_text("utf-8"))
        self.theme_ids = [t["id"] for t in self.tokens["color"]["themes"]]
        self.name = self.theme_ids[0]
        self._cache_dir = Path(tempfile.mkdtemp(prefix="tbui-"))
        self._icon_cache: dict = {}
        self._svg_cache: dict = {}
        self.values = self._compute(self.name)

    # ── 令牌 ────────────────────────────────────────────
    def _raw(self, family: str, theme: str) -> dict:
        out = {}
        first = self.theme_ids[0]
        for t in self.tokens.get(family, {}).get("tokens", []):
            v = t["value"]
            out[t["name"]] = v if isinstance(v, str) else v.get(theme, v.get(first))
        return out

    def _compute(self, theme: str) -> dict:
        raw = {}
        for fam in ("color", "shadow"):
            raw.update(self._raw(fam, theme))

        def resolve(name, depth=0):
            v = raw[name]
            m = _ALIAS.match(v)
            if m and depth < 16:
                return resolve(m.group(1), depth + 1)
            return v

        vals = {k: resolve(k) for k in raw}
        for fam in ("spacing", "radius", "size"):
            for t in self.tokens.get(fam, {}).get("tokens", []):
                vals[t["name"]] = t["value"]
        return vals

    def color(self, name: str) -> QtGui.QColor:
        return qcolor(self.values[name])

    def px(self, name: str) -> int:
        return int(float(str(self.values[name]).replace("px", "")))

    # ── 字体 ────────────────────────────────────────────
    @staticmethod
    def load_fonts():
        for f in sorted((HERE / "fonts").glob("*.ttf")):
            QtGui.QFontDatabase.addApplicationFont(str(f))

    @staticmethod
    def _font(families, px, weight=400, letter_spacing=None):
        f = QtGui.QFont()
        f.setFamilies(families)
        f.setPixelSize(px)
        f.setWeight({400: QtGui.QFont.Weight.Normal, 500: QtGui.QFont.Weight.Medium,
                     600: QtGui.QFont.Weight.DemiBold, 700: QtGui.QFont.Weight.Bold}[weight])
        if letter_spacing:
            f.setLetterSpacing(QtGui.QFont.SpacingType.PercentageSpacing, letter_spacing)
        return f

    def ui_font(self, px=13, weight=400):
        return self._font(UI_FAMILIES, px, weight)

    def display_font(self, px=18, weight=600):
        return self._font(DISPLAY_FAMILIES, px, weight)

    def label_font(self):
        """表头、导航分组名：11px 粗体，字间距 0.04em。"""
        return self._font(UI_FAMILIES, 11, 600, 104)

    def mono_font(self, px=12, weight=400):
        f = self._font(MONO_FAMILIES, px, weight)
        f.setStyleHint(QtGui.QFont.StyleHint.Monospace)
        if hasattr(QtGui.QFont, "Tag") and hasattr(f, "setFeature"):  # Qt 6.7+：关闭 JetBrains Mono 的连字
            try:
                f.setFeature(QtGui.QFont.Tag("calt"), 0)
            except Exception:
                pass
        return f

    # ── 图标 ────────────────────────────────────────────
    def _svg(self, name: str, color: str, stroke: float) -> bytes:
        key = (name, color, stroke)
        if key not in self._svg_cache:
            text = (HERE / "icons" / f"{name}.svg").read_text("utf-8")
            text = text.replace("currentColor", color).replace('stroke-width="2"', f'stroke-width="{stroke}"')
            self._svg_cache[key] = text.encode("utf-8")
        return self._svg_cache[key]

    def pixmap(self, name: str, color: str = "ink-muted", size: int = 16) -> QtGui.QPixmap:
        """color 可以是令牌名或 #hex。"""
        hexv = self.values.get(color, color)
        stroke = 1.75 if size >= 20 else 1.5
        app = QtGui.QGuiApplication.instance()
        dpr = app.devicePixelRatio() if app else 1.0
        scale = max(1, math.ceil(dpr))
        pm = QtGui.QPixmap(size * scale, size * scale)
        pm.fill(Qt.GlobalColor.transparent)
        r = QtSvg.QSvgRenderer(QtCore.QByteArray(self._svg(name, hexv, stroke)))
        p = QtGui.QPainter(pm)
        r.render(p)
        p.end()
        pm.setDevicePixelRatio(scale)
        return pm

    def icon(self, name: str, color: str = "ink-muted", size: int = 16, *, hover: str = "ink",
             checked: str = "signal-ink", disabled: str = "ink-disabled") -> QtGui.QIcon:
        """多状态图标：常态 color，悬停 hover，选中 checked，禁用 disabled。"""
        key = (self.name, name, color, size, hover, checked, disabled)
        if key in self._icon_cache:
            return self._icon_cache[key]
        ic = QtGui.QIcon()
        M, S = QtGui.QIcon.Mode, QtGui.QIcon.State
        ic.addPixmap(self.pixmap(name, color, size), M.Normal, S.Off)
        ic.addPixmap(self.pixmap(name, hover, size), M.Active, S.Off)
        ic.addPixmap(self.pixmap(name, disabled, size), M.Disabled, S.Off)
        ic.addPixmap(self.pixmap(name, checked, size), M.Normal, S.On)
        ic.addPixmap(self.pixmap(name, checked, size), M.Active, S.On)
        ic.addPixmap(self.pixmap(name, disabled, size), M.Disabled, S.On)
        self._icon_cache[key] = ic
        return ic

    def icon_file(self, name: str, color: str, size: int = 16) -> str:
        """给 QSS 的 url() 用：写出着色后的 SVG 文件，返回正斜杠路径。"""
        hexv = self.values.get(color, color)
        path = self._cache_dir / f"{name}-{hexv.strip('#')}.svg"
        if not path.exists():
            path.write_bytes(self._svg(name, hexv, 1.5))
        return path.as_posix()

    # ── QSS 与调色板 ────────────────────────────────────
    def qss(self) -> str:
        template = (HERE / "style.qss").read_text("utf-8")
        template = template.replace("/*@TONES*/", self._tone_rules())
        files = {
            "icon-chevron-down": self.icon_file("chevron-down", "ink-muted"),
            "icon-chevron-down-on-signal": self.icon_file("chevron-down", "on-signal"),
            "icon-x-faint": self.icon_file("x", "ink-faint", 14),
            "icon-x": self.icon_file("x", "ink", 14),
        }

        def sub(m):
            k = m.group(1)
            if k in files:
                return files[k]
            if k not in self.values:
                raise KeyError(f"style.qss 引用了不存在的令牌 @{k}")
            return self.values[k]

        return _VAR.sub(sub, template)

    def _tone_rules(self) -> str:
        out = []
        for t in TONES:
            fg = {"neutral": "@ink-muted", "signal": "@signal-ink"}.get(t, "@" + t)
            bg = {"neutral": "@bg-400", "signal": "@signal-soft"}.get(t, "@" + t + "-soft")
            out.append(f'QLabel[badge="{t}"] {{ background: {bg}; color: {fg}; border-radius: @radius-sm; '
                       f'padding: 0 6px; min-height: 18px; max-height: 18px; font-size: 12px; }}')
            out.append(f'QFrame[banner="{t}"] {{ background: {bg}; border-radius: @radius-lg; }}')
            out.append(f'QFrame[banner="{t}"] QLabel {{ color: @ink; background: transparent; }}')
            out.append(f'QLabel[tone="{t}"] {{ color: {fg}; }}')
        return "\n".join(out)

    def palette(self) -> QtGui.QPalette:
        c = self.color
        p = QtGui.QPalette()
        R, G = QtGui.QPalette.ColorRole, QtGui.QPalette.ColorGroup
        for g in (G.Active, G.Inactive):
            p.setColor(g, R.Window, c("bg-100"))
            p.setColor(g, R.WindowText, c("ink"))
            p.setColor(g, R.Base, c("bg-300"))
            p.setColor(g, R.AlternateBase, c("bg-200"))
            p.setColor(g, R.Text, c("ink"))
            p.setColor(g, R.Button, c("bg-300"))
            p.setColor(g, R.ButtonText, c("ink"))
            p.setColor(g, R.Highlight, c("bg-500"))
            p.setColor(g, R.HighlightedText, c("ink"))
            p.setColor(g, R.ToolTipBase, c("bg-300"))
            p.setColor(g, R.ToolTipText, c("ink"))
            p.setColor(g, R.PlaceholderText, c("ink-faint"))
            p.setColor(g, R.Link, c("signal-ink"))
            p.setColor(g, R.Mid, c("line"))
            p.setColor(g, R.Dark, c("bg-000"))
        for r in (R.WindowText, R.Text, R.ButtonText):
            p.setColor(G.Disabled, r, c("ink-disabled"))
        p.setColor(G.Disabled, R.Window, c("bg-100"))
        p.setColor(G.Disabled, R.Base, c("bg-300"))
        p.setColor(G.Disabled, R.Button, c("bg-300"))
        return p

    # ── 应用 ────────────────────────────────────────────
    def apply(self, app: QtWidgets.QApplication):
        app.setPalette(self.palette())
        app.setStyleSheet(self.qss())

    def set_theme(self, theme_id: str):
        if theme_id not in self.theme_ids:
            raise ValueError(theme_id)
        self.name = theme_id
        self.values = self._compute(theme_id)
        self._icon_cache.clear()
        app = QtWidgets.QApplication.instance()
        if app:
            self.apply(app)
        self.changed.emit(theme_id)

    def toggle(self):
        i = self.theme_ids.index(self.name)
        self.set_theme(self.theme_ids[(i + 1) % len(self.theme_ids)])


_theme: Theme | None = None


def theme() -> Theme:
    global _theme
    if _theme is None:
        _theme = Theme()
    return _theme


def install(app: QtWidgets.QApplication, theme_id: str | None = None) -> Theme:
    """一次完成：Fusion 样式、字体、调色板、QSS。"""
    app.setStyle("Fusion")
    Theme.load_fonts()
    t = theme()
    app.setFont(t.ui_font())
    if theme_id and theme_id != t.name:
        t.name = theme_id
        t.values = t._compute(theme_id)
    t.apply(app)
    return t


def repolish(w: QtWidgets.QWidget, **props):
    """设置动态属性并让 QSS 重新生效：repolish(btn, variant="primary", size="sm")。"""
    for k, v in props.items():
        w.setProperty(k, v)
    s = w.style()
    s.unpolish(w)
    s.polish(w)
    w.update()


def bind_icon(w, name: str, color: str = "ink-muted", size: int = 16, **kw):
    """给按钮设图标，并在切换主题时自动重新着色。"""
    t = theme()

    def refresh(*_):
        try:
            w.setIcon(t.icon(name, color, size, **kw))
            w.setIconSize(QtCore.QSize(size, size))
        except RuntimeError:  # 控件已销毁
            try:
                t.changed.disconnect(refresh)
            except Exception:
                pass

    refresh()
    t.changed.connect(refresh)
    return w
