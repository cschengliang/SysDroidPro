"""可取消的长任务，持续读取两条管道，安静阶段报告不确定进度。"""
from collections import deque
import threading
import time

from . import api, parsers as P
from ._base import error_for
from ._proc import ProcessStream
from .models import Progress, ShellResult


class JobMethods:
    def _run_job(self, serial, args, progress=None, cancel=None, message="正在执行…", measure=None, checked=True):
        if cancel and cancel.is_set():
            raise api.Cancelled("已取消")
        if progress:
            progress(Progress(None, message))
        if cancel and cancel.is_set():
            raise api.Cancelled("已取消")
        process = self._popen(self._adb_args(serial, *args))
        done = threading.Event()
        result, chunks = [], {"stdout": deque(maxlen=32), "stderr": deque(maxlen=32)}
        carry = {"stdout": "", "stderr": ""}

        def output(chunk, stream):
            chunks[stream].append(chunk)
            carry[stream] = (carry[stream] + chunk)[-16000:]
            percent = P.progress_percent(carry[stream])
            if progress and percent is not None:
                progress(Progress(percent, message))
            if "\r" in carry[stream] or "\n" in carry[stream]:
                carry[stream] = carry[stream].replace("\r", "\n").split("\n")[-1]

        def finish(code, out, err, stopped, error):
            result.append((code, error))
            done.set()

        handle = ProcessStream(process, output, finish)
        next_progress = time.monotonic() + .3
        try:
            while not done.wait(.05):
                if cancel and cancel.is_set():
                    handle.stop()
                    done.wait(2)
                    raise api.Cancelled("已取消")
                if progress and time.monotonic() >= next_progress:
                    progress(measure() if measure else Progress(None, message))
                    next_progress = time.monotonic() + .3
            if cancel and cancel.is_set():
                raise api.Cancelled("已取消")
            code, error = result[0]
            if error:
                raise error
            out, err = "".join(chunks["stdout"]), "".join(chunks["stderr"])
            error = error_for(code, out, err, serial, checked=checked)
            if error:
                raise error
            return ShellResult(out, err, code)
        finally:
            if handle.running:
                handle.stop()
