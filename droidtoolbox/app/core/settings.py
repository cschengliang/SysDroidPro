"""用户设置：便携模式，保存在程序目录 data/settings.ini。"""
from __future__ import annotations

from pathlib import Path

from tbui._qt import QtCore, Signal

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"

DEFAULTS = {
    "theme": "light",
    "adb_path": "",
    "fastboot_path": "",
    "scrcpy_path": "",
    "export_dir": str(Path.home() / "Documents" / "DroidToolbox"),
    "poll_interval_ms": 2000,
    "logcat_max_rows": 200_000,
    "logcat_tail": 2000,
    "auto_root": True,              # 设备连接后自动 adb root（user 固件自动跳过）
    "sidebar_collapsed": False,
    "last_page": "devices",
    # 投屏窗口位置与大小，空串表示由 scrcpy 决定
    "mirror_window_x": "",
    "mirror_window_y": "",
    "mirror_window_width": "",
    "mirror_window_height": "",
}


class Settings(QtCore.QObject):
    changed = Signal(str)

    def __init__(self, path: Path | None = None):
        super().__init__()
        path = path or DATA_DIR / "settings.ini"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._s = QtCore.QSettings(str(path), QtCore.QSettings.Format.IniFormat)

    def get(self, key: str):
        default = DEFAULTS[key]
        v = self._s.value(key, default)
        if isinstance(default, bool):
            return v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes")
        if isinstance(default, int):
            try:
                return int(v)
            except (TypeError, ValueError):
                return default
        return "" if v is None else str(v)

    def set(self, key: str, value):
        if key not in DEFAULTS:
            raise KeyError(key)
        self._s.setValue(key, value)
        self._s.sync()
        self.changed.emit(key)

    @property
    def file_name(self) -> str:
        return self._s.fileName()

    def tool_path(self, key: str) -> str | None:
        return self.get(key) or None

    def export_dir(self) -> Path:
        p = Path(self.get("export_dir"))
        p.mkdir(parents=True, exist_ok=True)
        return p
