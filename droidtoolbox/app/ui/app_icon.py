"""应用图标：signal 色圆角方块 + 手机图标。窗口 / 任务栏用 app_icon()，打包时 save_ico() 生成启动器的 .ico。"""
from __future__ import annotations

import tbui as ui
from tbui._qt import QtCore, QtGui, Qt

SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def render(size: int) -> QtGui.QPixmap:
    t = ui.theme()
    pm = QtGui.QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QtGui.QPainter(pm)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(t.color("signal"))
    m = max(1.0, size * 0.04)
    r = QtCore.QRectF(m, m, size - 2 * m, size - 2 * m)
    p.drawRoundedRect(r, size * 0.22, size * 0.22)
    g = round(size * 0.66)
    glyph = t.pixmap("smartphone", "on-signal", g)
    p.drawPixmap(QtCore.QRectF((size - g) / 2, (size - g) / 2, g, g), glyph, QtCore.QRectF(glyph.rect()))
    p.end()
    return pm


def app_icon() -> QtGui.QIcon:
    icon = QtGui.QIcon()
    for s in SIZES:
        icon.addPixmap(render(s))
    return icon


def save_ico(path: str) -> int:
    """写多尺寸 .ico（每个尺寸一个 PNG 条目，Windows Vista 起支持）；返回尺寸个数。
    Qt 自带的 ico 插件一次只写一个尺寸，所以这里按 ICO 文件格式自己拼。"""
    import struct
    pngs = []
    for s in SIZES:
        buf = QtCore.QBuffer()
        buf.open(QtCore.QIODevice.OpenModeFlag.WriteOnly)
        render(s).save(buf, "PNG")
        pngs.append((s, bytes(buf.data())))
    head = struct.pack("<HHH", 0, 1, len(pngs))
    offset = len(head) + 16 * len(pngs)
    entries, blobs = b"", b""
    for s, data in pngs:
        dim = 0 if s >= 256 else s                      # 256 在目录项里写作 0
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset + len(blobs))
        blobs += data
    with open(path, "wb") as f:
        f.write(head + entries + blobs)
    return len(pngs)
