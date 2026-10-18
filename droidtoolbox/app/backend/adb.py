"""真实的标准库 ADB 后端。实现按功能拆分，公共接口严格继承 api.Backend。"""
from .api import Backend
from ._apps import AppMethods
from ._base import BackendBase
from ._crashes import CrashMethods
from ._devices import DeviceMethods
from ._files import FileMethods
from ._jobs import JobMethods
from ._mirror import MirrorMethods
from ._streams import StreamMethods
from ._system import SystemMethods


class AdbBackend(DeviceMethods, StreamMethods, SystemMethods, AppMethods, FileMethods,
                 CrashMethods, MirrorMethods, JobMethods, BackendBase, Backend):
    name = "adb"

    def __init__(self, runner=None):
        super().__init__(runner)
