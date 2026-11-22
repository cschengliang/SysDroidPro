"""把阻塞的后端调用放进线程池，结果、进度、流回调都转回 Qt 主线程。

    job = manager.run(backend.device_detail, serial, done=self.show, error=self.fail)
    job = manager.run(backend.install, serial, paths, opts, progress=bar.update, cancellable=True)
    job.cancel()

    on_lines = on_ui(self.append)            # 后端线程里调用 on_lines(...)，self.append 在主线程执行
"""
from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from tbui._qt import QtCore, Signal

from ..backend.api import Cancelled

log = logging.getLogger(__name__)


def _alive(fn) -> bool:
    """回调绑定的控件已销毁时跳过。"""
    owner = getattr(fn, "__self__", None)
    if owner is None or not isinstance(owner, QtCore.QObject):
        return True
    return object_alive(owner)


def object_alive(owner):
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

    def __init__(self, manager, fn, args, kwargs, done, error, progress, cancellable, guard, settled):
        super().__init__()
        self._manager = manager
        self._fn, self._args, self._kwargs = fn, args, kwargs
        self._done, self._error, self._progress = done, error, progress
        self._guard, self._settled = guard, settled
        self._future = None
        self._cancel = threading.Event() if cancellable else None
        self.finished = False
        self._event.connect(self._dispatch)
        if progress is not None:
            self._kwargs["progress"] = lambda p: self._event.emit("progress", p)
        if cancellable:
            self._kwargs["cancel"] = self._cancel

    def cancel(self):
        if self._cancel is not None:
            self._cancel.set()
            if self._future is not None and self._future.cancel():
                self._dispatch("error", Cancelled("已取消"))

    @property
    def cancelled(self) -> bool:
        return self._cancel is not None and self._cancel.is_set()

    def _work(self):
        if self._manager.closed:
            return
        try:
            if self.cancelled:
                raise Cancelled("已取消")
            result = self._fn(*self._args, **self._kwargs)
        except BaseException as e:  # noqa: BLE001 — 任何异常都要送回界面
            self._event.emit("error", e)
        else:
            self._event.emit("done", result)

    def _dispatch(self, kind: str, payload: Any):
        if self.finished:
            return
        if kind != "progress":
            self.finished = True
            self._manager._live.discard(self)
        if self._manager.closed:
            return
        if kind == "progress":
            if self._progress and not self.cancelled and self._valid() and _alive(self._progress):
                self._progress(payload)
            return
        if self._settled is not None and _alive(self._settled):
            self._settled()
        if not self._valid():
            return
        if kind == "done":
            if self._done and _alive(self._done):
                self._done(payload)
        elif isinstance(payload, Cancelled) or self.cancelled:
            if self._error and _alive(self._error):
                self._error(payload if isinstance(payload, Cancelled) else Cancelled("已取消"))
        elif self._error is not None:
            if _alive(self._error):
                self._error(payload)
        elif self._manager.report is not None:
            self._manager.report(payload)

    def _valid(self):
        return self._guard is None or self._guard()


class JobManager:
    """One application owns its worker pool, streams and shutdown callbacks."""

    def __init__(self, report=None, *, max_workers=16):
        self.report = report
        self.closed = False
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._live: set[Job] = set()
        self._lock = threading.Lock()
        self._streams = {}
        self._cleanups = []

    def run(self, fn, *args, done=None, error=None, progress=None, cancellable=False,
            guard=None, settled=None, **kwargs) -> Job:
        """Submit on the UI thread; guard limits delivery, not execution."""
        job = Job(self, fn, args, kwargs, done, error, progress, cancellable, guard, settled)
        if self.closed:
            job.finished = True
        else:
            self._live.add(job)
            job._future = self._pool.submit(job._work)
        return job

    def own_stream(self, stream):
        """May run on a worker (e.g. open_shell); close a late result immediately."""
        with self._lock:
            if not self.closed:
                self._streams = {k: s for k, s in self._streams.items() if s.running}
                self._streams[id(stream)] = stream
                return stream
        self._cleanup(stream.stop)
        return stream

    def add_cleanup(self, fn):
        self._cleanups.append(fn)

    def close(self):
        with self._lock:
            if self.closed:
                return
            self.closed = True
            streams, self._streams = self._streams, {}
        for job in tuple(self._live):
            job.cancel()
        self._pool.shutdown(wait=False, cancel_futures=True)
        for job in self._live:
            job.finished = True
        self._live.clear()
        for stream in streams.values():
            self._cleanup(stream.stop)
        for fn in reversed(self._cleanups):
            self._cleanup(fn)
        self._cleanups.clear()

    @staticmethod
    def _cleanup(fn):
        try:
            fn()
        except Exception:
            log.exception("Resource cleanup failed")

    def on_ui(self, fn):
        return on_ui(fn, valid=lambda: not self.closed)


class _Bridge(QtCore.QObject):
    _call = Signal(object)

    def __init__(self, fn, valid):
        super().__init__()
        self._fn = fn
        self._valid = valid
        self._call.connect(self._run)

    def _run(self, args):
        if (self._valid is None or self._valid()) and _alive(self._fn):
            self._fn(*args)


def on_ui(fn: Callable, valid=None) -> Callable:
    """包装回调：在任意线程调用返回的函数，fn 都在主线程执行。必须在主线程调用 on_ui。"""
    bridge = _Bridge(fn, valid)

    def call(*args):
        try:
            bridge._call.emit(args)
        except RuntimeError:  # 主线程已退出
            pass
    call._bridge = bridge  # 保持引用
    return call
