"""无控制台进程及受 stop 锁保护的块输出流，只使用标准库。"""
from __future__ import annotations

import codecs
from collections import deque
import queue
import subprocess
import threading
import time

from .api import BackendError, CommandFailed, StreamHandle


class Runner:
    def run(self, args, timeout, input=None):
        result = subprocess.run(args, input=input, capture_output=True, timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return result.returncode, result.stdout, result.stderr

    def popen(self, args, *, env=None):
        return subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def terminate(process):
    """等待本地进程退出并在必要时强杀；由读线程排空管道。"""
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=0.7)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=0.7)


class ProcessStream(StreamHandle):
    def __init__(self, process, on_output, on_finish, *, tick=None, health=None):
        self.process = process
        self._output, self._finish = on_output, on_finish
        self._tick, self._health = tick, health
        self._stop = threading.Event()
        self._closing = threading.Event()
        self._lock = threading.RLock()
        self._queue = queue.Queue(maxsize=128)
        self._readers = []
        self._tails = {"stdout": deque(maxlen=20), "stderr": deque(maxlen=20)}
        self._thread = threading.Thread(target=self._run, daemon=True, name="dtb-stream")
        self._thread.start()

    def emit(self, fn, *args):
        with self._lock:
            if not self._stop.is_set():
                fn(*args)

    def _put(self, item):
        while not self._stop.is_set() and not self._closing.is_set():
            try:
                self._queue.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def _read(self, pipe, stream):
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        try:
            while True:
                data = pipe.read1(4096) if hasattr(pipe, "read1") else pipe.read(4096)
                if not data:
                    break
                self._put((stream, decoder.decode(data)))
            last = decoder.decode(b"", final=True)
            if last:
                self._put((stream, last))
        finally:
            self._put((stream, None))

    def _run(self):
        error = None
        code = None
        closed = set()
        next_health = time.monotonic() + 1
        try:
            for stream in ("stdout", "stderr"):
                reader = threading.Thread(target=self._read, args=(getattr(self.process, stream), stream), daemon=True)
                self._readers.append(reader)
                reader.start()
            while len(closed) < 2 and not self._stop.is_set():
                try:
                    stream, chunk = self._queue.get(timeout=0.05)
                    if chunk is None:
                        closed.add(stream)
                    else:
                        self._tails[stream].append(chunk)
                        self.emit(self._output, chunk, stream)
                except queue.Empty:
                    pass
                if self._tick:
                    self.emit(self._tick)
                if self._health and time.monotonic() >= next_health and self.process.poll() is None:
                    self._health()
                    next_health = time.monotonic() + 1
            code = self.process.wait() if not self._stop.is_set() else None
        except BackendError as exc:
            error = exc
        except Exception as exc:
            error = CommandFailed("读取进程输出失败", str(exc))
        finally:
            self._closing.set()
            terminate(self.process)
            for reader in self._readers:
                reader.join(timeout=1)
            for stream in ("stdin", "stdout", "stderr"):
                pipe = getattr(self.process, stream, None)
                if pipe:
                    pipe.close()
            out = "".join(self._tails["stdout"])[-8000:]
            err = "".join(self._tails["stderr"])[-8000:]
            self._finish(code, out, err, self._stop.is_set(), error)

    def stop(self):
        with self._lock:
            self._stop.set()
        terminate(self.process)

    @property
    def running(self):
        return self._thread.is_alive() and not self._stop.is_set()
