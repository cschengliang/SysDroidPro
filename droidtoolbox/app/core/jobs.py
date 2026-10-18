"""把阻塞的后端调用放进线程池，结果、进度、流回调都转回 Qt 主线程。

    job = run(backend.device_detail, serial, done=self.show, error=self.fail)
    job = run(backend.install, serial, paths, opts, progress=bar.update, cancellable=True)  # fn 收到 progress= / cancel=
    job.cancel()

    on_lines = on_ui(self.append)            # 后端线程里调用 on_lines(...)，self.append 在主线程执行
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from tbui._qt import QtCore, Signal

from ..backend.api import Cancelled

_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="job")
_live: set["Job"] = set()          # 运行中的任务，防止被垃圾回收
_default_error: Callable[[BaseException], None] | None = None


def set_default_error_handler(fn: Callable[[BaseException], None]):
    """没有传 error= 的任务失败时调用（主窗口用它弹出提示）。"""
    global _default_error
    _default_error = fn


def _alive(fn) -> bool:
    """回调绑定的控件已销毁时跳过。"""
    owner = getattr(fn, "__self__", None)
    if owner is None or not isinstance(owner, QtCore.QObject):
        return True
    try:
        import shiboken6
        return shiboken6.isValid(owner)
    except ImportError:  # PyQt6
        try:
            owner.objectName()
            return True
        except RuntimeError:
            return False


class Job(QtCore.QObject):
    """一次后台调用。Job 对象住在主线程，工作线程通过信号投递结果（自动排队到主线程）。"""

    _event = Signal(str, object)

    def __init__(self, fn, args, kwargs, done, error, progress, cancellable):
        super().__init__()
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self._done, self._error, self._progress = done, error, progress
        self._cancel = threading.Event() if cancellable else None
        self.finished = False
        self._event.connect(self._dispatch)
        if progress is not None:
            self._kwargs["progress"] = lambda p: self._event.emit("progress", p)
        if cancellable:
            self._kwargs["cancel"] = self._cancel

    def start(self) -> "Job":
        _live.add(self)
        _pool.submit(self._work)
        return self

    def cancel(self):
        if self._cancel is not None:
            self._cancel.set()

    @property
    def cancelled(self) -> bool:
        return self._cancel is not None and self._cancel.is_set()

    def _work(self):
        try:
            result = self._fn(*self._args, **self._kwargs)
        except BaseException as e:  # noqa: BLE001 — 任何异常都要送回界面
            self._event.emit("error", e)
        else:
            self._event.emit("done", result)

    def _dispatch(self, kind: str, payload: Any):
        if kind == "progress":
            if self._progress and _alive(self._progress) and not self.finished:
                self._progress(payload)
            return
        self.finished = True
        _live.discard(self)
        if kind == "done":
            if self._done and _alive(self._done):
                self._done(payload)
        elif isinstance(payload, Cancelled) or self.cancelled:
            if self._error and _alive(self._error):
                self._error(payload if isinstance(payload, Cancelled) else Cancelled("已取消"))
        elif self._error is not None:
            if _alive(self._error):
                self._error(payload)
        elif _default_error is not None:
            _default_error(payload)


def run(fn: Callable, *args, done: Callable[[Any], None] | None = None,
        error: Callable[[BaseException], None] | None = None,
        progress: Callable[[Any], None] | None = None, cancellable: bool = False, **kwargs) -> Job:
    """在线程池里执行 fn(*args, **kwargs)。必须在主线程调用。"""
    return Job(fn, args, dict(kwargs), done, error, progress, cancellable).start()


class _Bridge(QtCore.QObject):
    _call = Signal(object)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self._call.connect(self._run)

    def _run(self, args):
        if _alive(self._fn):
            self._fn(*args)


def on_ui(fn: Callable) -> Callable:
    """包装回调：在任意线程调用返回的函数，fn 都在主线程执行。必须在主线程调用 on_ui。"""
    bridge = _Bridge(fn)

    def call(*args):
        try:
            bridge._call.emit(args)
        except RuntimeError:  # 主线程已退出
            pass
    call._bridge = bridge  # 保持引用
    return call
