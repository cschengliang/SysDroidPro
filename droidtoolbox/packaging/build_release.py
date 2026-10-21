"""构建 Windows 绿色版（免安装，解压即用）。

    ..\\python-3.14.8-embed-amd64\\python.exe packaging\\build_release.py            # 构建 + 自检 + 打 zip
    ..\\python-3.14.8-embed-amd64\\python.exe packaging\\build_release.py --no-zip   # 只生成目录

输出 <仓库>/dist/SysDroidPro-<版本>-win64/ 与同名 .zip：

    SysDroidPro.exe           启动器（runtime\\pythonw.exe droidtoolbox\\main.py，参数原样传递）
    SysDroidPro-debug.bat     带控制台启动，便于看异常
    使用说明.txt               版本、提交、目录说明
    runtime\\                  精简后的嵌入式 Python 3.14 + PySide6（只保留 QtCore / QtGui / QtWidgets / QtSvg）
    android-toolbox-qt-style\\tbui\\   界面样式包
    droidtoolbox\\             main.py、app\\（已预编译 .pyc）、tool\\（adb、scrcpy、Winscope、Perfetto）
                               设置与命令库保存在 droidtoolbox\\data\\（首次运行时创建）

不打包：测试、开发文档、pip / pytest 等开发依赖、Qt 的 WebEngine / QML / 3D / 设计器等未使用模块、
Perfetto 的 .pdb 调试符号与原始下载包、未使用的 tool\\tools\\terminal-*。
"""
from __future__ import annotations

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

APP = Path(__file__).resolve().parents[1]            # droidtoolbox/
REPO = APP.parent
PY = REPO / "python-3.14.8-embed-amd64"
KIT = REPO / "android-toolbox-qt-style"
SITE = PY / "Lib" / "site-packages"
CSC = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"

QT_MODULES = ("QtCore", "QtGui", "QtWidgets", "QtSvg")
PYSIDE_FILES = ["__init__.py", "_config.py", "_git_pyside_version.py", "pyside6.abi3.dll",
                *(f"{m}.pyd" for m in QT_MODULES), *(f"Qt6{m[2:]}.dll" for m in QT_MODULES),
                "concrt140.dll", "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_codecvt_ids.dll",
                "vcruntime140.dll", "vcruntime140_1.dll"]
QT_PLUGINS = ["platforms/qwindows.dll", "platforms/qoffscreen.dll", "iconengines/qsvgicon.dll", "styles/qmodernwindowsstyle.dll",
              "imageformats/qsvg.dll", "imageformats/qico.dll", "imageformats/qjpeg.dll", "imageformats/qgif.dll"]
SITE_KEEP = ["shiboken6", "shiboken6-6.12.0.dist-info", "pyside6_essentials-6.12.0.dist-info"]
RUNTIME_SKIP = {"get-pip.py", "pip.ini", "Lib", "__pycache__", "Scripts"}
TOOLS = ["platform-tools", "scrcpy-win64-v4.1", "tools/winscope-android17",
         "perfetto-local/ui", "perfetto-local/bin/windows-amd64/trace_processor_shell.exe",
         "perfetto-local/traces", "perfetto-local/README.md", "perfetto-local/release.json"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "*.pdb")


def log(msg: str):
    print(msg, flush=True)


def version() -> str:
    text = (APP / "app" / "__init__.py").read_text("utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def git_info() -> str:
    try:
        rev = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain"], capture_output=True, text=True).stdout.strip()
        return rev + ("（含未提交的修改）" if dirty else "") if rev else "未知"
    except OSError:
        return "未知"


def copy(src: Path, dst: Path):
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dst, ignore=IGNORE)
    else:
        shutil.copy2(src, dst)


def size_of(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size


def mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


# 各部分 ----------------------------------------------------------------
def build_runtime(out: Path):
    rt = out / "runtime"
    for f in PY.iterdir():
        if f.name not in RUNTIME_SKIP:
            copy(f, rt / f.name)
    site = rt / "Lib" / "site-packages"
    for name in SITE_KEEP:
        copy(SITE / name, site / name)
    for name in PYSIDE_FILES:
        copy(SITE / "PySide6" / name, site / "PySide6" / name)
    for name in QT_PLUGINS:
        copy(SITE / "PySide6" / "plugins" / name, site / "PySide6" / "plugins" / name)
    for lic in ("LICENSES", "LICENSE"):          # 许可证文本（如果有）
        src = SITE / "PySide6" / lic
        if src.exists():
            copy(src, site / "PySide6" / lic)


def build_app(out: Path, with_tools: bool):
    dst = out / "droidtoolbox"
    copy(APP / "main.py", dst / "main.py")
    copy(APP / "README.md", dst / "README.md")
    copy(APP / "app", dst / "app")
    copy(KIT / "tbui", out / "android-toolbox-qt-style" / "tbui")
    if with_tools:
        for t in TOOLS:
            src = APP / "tool" / t
            if not src.exists():
                raise SystemExit(f"缺少附带工具：{src}")
            copy(src, dst / "tool" / t)


def precompile(out: Path):
    """用打包后的 runtime 预编译，.pyc 的版本标记与运行时一致。"""
    py = out / "runtime" / "python.exe"
    targets = [out / "droidtoolbox" / "app", out / "droidtoolbox" / "main.py", out / "android-toolbox-qt-style" / "tbui",
               out / "droidtoolbox" / "tool" / "tools" / "winscope-android17"]
    targets = [str(t) for t in targets if t.exists()]
    subprocess.run([str(py), "-m", "compileall", "-q", "-j", "0", *targets], check=True)


def make_icon(path: Path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    sys.path[:0] = [str(APP), str(KIT)]
    from tbui._qt import QtWidgets
    from app.ui import style
    from app.ui.app_icon import save_ico
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    style.install(app, "light")
    if save_ico(str(path)) < 1:
        raise SystemExit("生成图标失败")


def build_launcher(out: Path, ver: str, tmp: Path):
    if not CSC.exists():
        raise SystemExit(f"找不到 C# 编译器：{CSC}（.NET Framework 4.x 自带）")
    ico = tmp / "app.ico"
    make_icon(ico)
    shutil.copy2(ico, out / "droidtoolbox" / "app.ico")
    src = tmp / "launcher.cs"
    nums = (ver.split(".") + ["0", "0", "0"])[:3]
    text = (APP / "packaging" / "launcher.cs").read_text("utf-8").replace("@VERSION@", ".".join(n if n.isdigit() else "0" for n in nums))
    src.write_text(text, "utf-8-sig")             # 带 BOM，csc 才按 UTF-8 读中文
    r = subprocess.run([str(CSC), "/nologo", "/target:winexe", "/optimize+", "/codepage:65001", f"/win32icon:{ico}",
                        "/r:System.Windows.Forms.dll", f"/out:{out / 'SysDroidPro.exe'}", str(src)],
                       capture_output=True, text=True, encoding="mbcs", errors="replace")
    if r.returncode:
        raise SystemExit("编译启动器失败：\n" + r.stdout + r.stderr)


def write_extras(out: Path, ver: str):
    (out / "SysDroidPro-debug.bat").write_text(
        "@echo off\r\nrem 带控制台启动，异常堆栈会打印在这里。例如 SysDroidPro-debug.bat --mock\r\n"
        "\"%~dp0runtime\\python.exe\" \"%~dp0droidtoolbox\\main.py\" %*\r\npause\r\n", "utf-8")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    (out / "使用说明.txt").write_text(f"""SysDroidPro {ver}（Android 系统工程师工具箱，Windows x64 绿色版）
构建时间：{now}
源码提交：{git_info()}

运行
    双击 SysDroidPro.exe。
    SysDroidPro.exe --mock      演示数据，不连接设备
    SysDroidPro-debug.bat       带控制台窗口，出现异常时可以看到堆栈

说明
    - 免安装：整个文件夹可以放在任意位置（路径中可以有中文和空格），不写注册表。
    - 设置、命令库、执行历史、快照保存在 droidtoolbox\\data\\；升级时把这个目录复制到新版本即可保留。
    - 已附带 adb（platform-tools）、scrcpy 4.1、Winscope（Android 17）、本地 Perfetto v58.2（UI + trace_processor_shell）。
      偏好设置里可以改用自己的 adb / scrcpy 路径。
    - 移动或删除文件夹前请先关闭 SysDroidPro（功能启动页里运行的 Winscope / Perfetto 会随之停止）。

目录
    SysDroidPro.exe             启动器
    runtime\\                    嵌入式 Python 3.14 + PySide6 6.12（精简）
    android-toolbox-qt-style\\   界面样式包
    droidtoolbox\\               程序（app\\）与附带工具（tool\\）
""", "utf-8-sig")


def smoke_test(out: Path):
    """用打包后的 runtime 在无窗口模式下启动主窗口、逐页打开（演示数据），确认没有缺模块 / 缺 DLL。"""
    script = out / "_smoke.py"
    script.write_text(r'''
import os, sys, traceback
from pathlib import Path
root = Path(__file__).resolve().parent
sys.path[:0] = [str(root / "droidtoolbox"), str(root / "android-toolbox-qt-style")]
os.environ["QT_QPA_PLATFORM"] = "offscreen"
errors = []
sys.excepthook = lambda t, v, tb: errors.append("".join(traceback.format_exception(t, v, tb)))
import tempfile, time
from tbui._qt import QtWidgets, QtSvg  # noqa
from app.backend.mock import MockBackend
import app.backend.adb  # noqa: F401  真实后端也要能导入
from app.core.commands import CommandStore, PRESETS
from app.core.settings import Settings
from app.core.state import AppState
from app.main_window import MainWindow
from app.ui import style
from app.ui.app_icon import app_icon
qapp = QtWidgets.QApplication([])
style.install(qapp, "light")
qapp.setWindowIcon(app_icon())
tmp = Path(tempfile.mkdtemp())
s = Settings(tmp / "s.ini"); s.set("auto_root", False)
st = AppState(MockBackend(), s, CommandStore(tmp / "c.json", tmp / "h.json"))
w = MainWindow(st); w.show(); st.start()
def pump(sec):
    end = time.time() + sec
    while time.time() < end:
        qapp.processEvents(); time.sleep(0.02)
pump(1.0)
st.select("3A281FDH2000XY")
for pid in list(w.pages):
    w.open_page(pid); pump(0.4)
w.close(); pump(0.2)
assert len(PRESETS) > 100
if errors:
    print("\n".join(errors)); sys.exit(1)
print(f"smoke ok: {len(w.pages)} pages")
''', "utf-8")
    try:
        r = subprocess.run([str(out / "runtime" / "python.exe"), str(script)], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=180,
                           env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    finally:
        script.unlink(missing_ok=True)
    print(r.stdout.strip() or r.stderr.strip()[-2000:])
    if r.returncode:
        raise SystemExit("自检失败：\n" + r.stderr[-4000:])
    for cache in (out / "droidtoolbox").glob("data"):          # 自检不应留下用户数据
        shutil.rmtree(cache, ignore_errors=True)


def make_zip(out: Path) -> Path:
    z = out.parent / (out.name + ".zip")      # 不能用 with_suffix：版本号里的点会被当成扩展名
    z.unlink(missing_ok=True)
    with zipfile.ZipFile(z, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in sorted(out.rglob("*")):
            if f.is_file():
                zf.write(f, Path(out.name) / f.relative_to(out))
    return z


def main() -> int:
    ap = argparse.ArgumentParser(description="构建 SysDroidPro Windows 绿色版")
    ap.add_argument("--out", type=Path, default=REPO / "dist", help="输出目录（默认 <仓库>/dist）")
    ap.add_argument("--no-zip", action="store_true", help="不打 zip")
    ap.add_argument("--no-tools", action="store_true", help="不附带 tool\\ 下的 adb / scrcpy / Winscope / Perfetto")
    ap.add_argument("--no-check", action="store_true", help="跳过自检")
    args = ap.parse_args()

    ver = version()
    out = args.out.resolve() / f"SysDroidPro-{ver}-win64"
    if out.exists():
        if out.parent.name != "dist" and not (out / "SysDroidPro.exe").exists():
            raise SystemExit(f"输出目录已存在且不像之前的构建结果，拒绝删除：{out}")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    log(f"构建 SysDroidPro {ver} → {out}")
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        log("· 运行时（精简 PySide6）");          build_runtime(out)
        log("· 程序与样式包" + ("" if args.no_tools else " + 附带工具")); build_app(out, not args.no_tools)
        log("· 预编译 .pyc");                    precompile(out)
        log("· 图标与启动器");                   build_launcher(out, ver, tmp)
        write_extras(out, ver)
    if not args.no_check:
        log("· 自检（无窗口启动并逐页打开）");    smoke_test(out)
    parts = [("runtime", out / "runtime"), ("程序", out / "droidtoolbox" / "app"), ("附带工具", out / "droidtoolbox" / "tool")]
    log("完成：" + mb(size_of(out)) + "（" + "，".join(f"{n} {mb(size_of(p))}" for n, p in parts if p.exists()) + "）")
    if not args.no_zip:
        log("· 打包 zip")
        z = make_zip(out)
        log(f"zip：{z}（{mb(z.stat().st_size)}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
