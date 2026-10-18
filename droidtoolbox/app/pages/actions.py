"""多个页面共用的操作：安装 APK、无线连接。"""
from __future__ import annotations

import os

import tbui as ui

from ..backend.models import AndroidUser, InstallOptions
from ..ui.common import Dialog, TaskStrip, ask_text, hbox, label, open_files

REBOOT_TEXT = {"system": "重启系统", "recovery": "重启到 Recovery", "bootloader": "重启到 Bootloader",
               "fastboot": "重启到 Fastbootd", "sideload": "重启到 Sideload", "poweroff": "关机"}


def install_apks(page, serial: str, strip: TaskStrip, paths: list[str] | None = None, done=None,
                 users: list[AndroidUser] | None = None, current_user: int | None = None):
    """选择 APK → 安装选项（含目标用户）→ 在 strip 中显示进度。paths 为空时弹出文件对话框。"""
    paths = paths or open_files(page, "选择要安装的 APK", "Android 应用 (*.apk);;所有文件 (*)", multi=True)
    if not paths:
        return
    names = [os.path.basename(p) for p in paths]
    d = Dialog(page, "安装应用", "安装")
    d.add_text(("将安装 <code>" + names[0] + "</code>" if len(names) == 1 else
                f"将以 split APK 方式安装 {len(names)} 个文件：<br><code>" + "<br>".join(names) + "</code>"))
    target = None
    if users and len(users) > 1:
        target = ui.select([("all", "所有用户")] + [(str(u.id), f"用户 {u.id} · {u.name}") for u in users],
                           str(current_user) if current_user is not None else "all")
        d.add(hbox(label("安装到"), target, None))
    sw_replace = d.add(ui.Switch("覆盖安装（保留数据，-r）", True))
    sw_down = d.add(ui.Switch("允许降级（-d）", False))
    sw_grant = d.add(ui.Switch("授予全部运行时权限（-g）", False))
    sw_test = d.add(ui.Switch("允许测试包（-t）", True))
    if not d.run():
        return
    user = None if target is None or target.currentData() == "all" else int(target.currentData())
    opts = InstallOptions(sw_replace.isChecked(), sw_down.isChecked(), sw_grant.isChecked(), sw_test.isChecked(), user)
    title = f"正在安装 {names[0]}" if len(names) == 1 else f"正在安装 {len(names)} 个 APK"
    if user is not None:
        title += f"（用户 {user}）"

    def ok(_):
        page.ctx.notify("ok", "安装成功", "、".join(names))
        if done:
            done()
    strip.run(title, page.ctx.backend.install, serial, paths, opts, done=ok, ok_text="安装成功")


def connect_dialog(page):
    """adb connect。"""
    addr = ask_text(page, "无线连接", "设备地址", placeholder="192.168.1.23:5555",
                    hint="设备与电脑在同一网络；Android 11+ 先在“无线调试”中配对。端口默认 5555。", ok_text="连接",
                    check=lambda s: None if s.strip() and " " not in s.strip() else "请输入 IP 或 IP:端口")
    if not addr:
        return
    page.ctx.run(page.ctx.backend.connect, addr,
                 done=lambda serial: (page.ctx.notify("ok", "已连接", serial), page.ctx.state.refresh_devices(),
                                      page.ctx.state.select(serial)),
                 error=lambda e: page.ctx.report(e))


def pair_dialog(page):
    """adb pair（Android 11+ 无线调试）。"""
    d = Dialog(page, "无线调试配对", "配对")
    d.add_text("在手机“开发者选项 → 无线调试 → 使用配对码配对设备”中查看 IP 地址、端口和六位配对码。配对成功后再用“无线连接”连接调试端口。")
    addr = d.add(ui.TextField("配对地址", "192.168.1.23:37215", mono=True))
    code = d.add(ui.TextField("配对码", "6 位数字", mono=True))

    def validate():
        e1 = None if ":" in addr.edit.text() else "需要 IP:端口（配对端口与调试端口不同）"
        e2 = None if code.edit.text().strip().isdigit() and len(code.edit.text().strip()) == 6 else "配对码是 6 位数字"
        addr.set_error(e1)
        code.set_error(e2)
        return e1 or e2
    d.validate = validate
    if not d.run():
        return
    page.ctx.run(page.ctx.backend.pair, addr.edit.text().strip(), code.edit.text().strip(),
                 done=lambda _: page.ctx.notify("ok", "配对成功", "现在可以用“无线连接”连接设备的调试端口。"),
                 error=lambda e: page.ctx.report(e))
