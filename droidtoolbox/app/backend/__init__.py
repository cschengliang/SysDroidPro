"""后端：api.py 是契约，mock.py 是演示实现，adb.py（待实现）是真实实现。"""
from .api import Backend, BackendError, Cancelled

__all__ = ["Backend", "BackendError", "Cancelled", "create_backend"]


def create_backend(mock: bool = False) -> Backend:
    """mock=True 或 adb.py 还不存在时返回演示后端；adb.py 存在但有错误时直接抛出，不静默降级。"""
    if not mock:
        try:
            from .adb import AdbBackend
        except ModuleNotFoundError as e:
            if e.name != __name__ + ".adb":
                raise
        else:
            return AdbBackend()
    from .mock import MockBackend
    return MockBackend()
