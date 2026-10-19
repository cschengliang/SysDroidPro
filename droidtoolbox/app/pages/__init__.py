"""页面注册表：侧栏分组与顺序。新页面在这里登记即可出现在导航中。"""
from .adb_commands import AdbCommandsPage
from .apps import AppsPage
from .bugreport import BugreportPage
from .devices import DevicesPage
from .files import FilesPage
from .launcher import LauncherPage
from .logcat import LogcatPage
from .mirror import MirrorPage
from .prefs import PrefsPage
from .props import PropsPage
from .services import ServicesPage
from .settings_db import SettingsDbPage
from .shell import ShellPage

GROUPS = [
    ("设备", [DevicesPage]),
    ("调试", [AdbCommandsPage, ShellPage, LogcatPage, BugreportPage]),
    ("系统", [PropsPage, SettingsDbPage, ServicesPage]),
    ("管理", [AppsPage, FilesPage, MirrorPage]),
    ("工具箱", [LauncherPage, PrefsPage]),
]
