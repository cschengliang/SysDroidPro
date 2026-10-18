"""工具路径解析；不依赖当前工作目录。"""
import os
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[2]


def find_tool(name, configured=None, adb_path=None):
    filename = name + (".exe" if os.name == "nt" else "")
    if configured:
        path = Path(configured).expanduser()
        return str(path.resolve()) if path.is_file() else None
    candidates = []
    if name == "fastboot" and adb_path:
        candidates.append(Path(adb_path).with_name(filename))
    if name == "scrcpy":
        candidates.append(ROOT / "tool/scrcpy-win64-v4.1" / filename)
    else:
        candidates.append(ROOT / "tool/platform-tools" / filename)
    for path in candidates:
        if path.is_file():
            return str(path.resolve())
    found = shutil.which(name)
    if found:
        return str(Path(found).resolve())
    if name != "scrcpy":
        sdk_dirs = [os.environ.get("ANDROID_HOME"), os.environ.get("ANDROID_SDK_ROOT")]
        if os.environ.get("LOCALAPPDATA"):
            sdk_dirs.append(str(Path(os.environ["LOCALAPPDATA"]) / "Android/Sdk"))
        for directory in filter(None, sdk_dirs):
            path = Path(directory) / "platform-tools" / filename
            if path.is_file():
                return str(path.resolve())
    return None
