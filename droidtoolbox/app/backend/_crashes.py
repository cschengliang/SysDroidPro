"""崩溃记录、按毫秒标识读取 DropBox 和 Bugreport。"""
from dataclasses import replace
from pathlib import Path
import re
import threading
import time

from . import api, parsers as P
from ._base import detail, error_for, quote_args, text
from .models import CrashFile


class CrashMethods:
    def _device_timezone(self, serial):
        return P.timezone_offset(self._shell(serial, "date +%z").strip())

    def list_crash_files(self, serial):
        offset = self._device_timezone(serial)
        rows = []
        for kind, path in (("tombstone", "/data/tombstones"), ("anr", "/data/anr")):
            try:
                entries = P.file_entries(self._shell(serial, quote_args("ls", "-l", path + "/")), path)
                found = [CrashFile(kind, item.path, item.name, P.timestamp(item.mtime, offset), item.size) for item in entries
                         if not item.is_dir and not item.name.endswith(".pb")]
                for item in sorted(found, key=lambda row: row.mtime or 0, reverse=True)[:20]:
                    result = self.shell(serial, quote_args("head", "-c", "4096", item.path))
                    if result.exit_code == 0:
                        process, summary = P.crash_summary(result.stdout)
                        found[found.index(item)] = replace(item, process=process, summary=summary)
                rows += found
            except (api.DeviceNotFound, api.DeviceUnauthorized):
                raise
            except api.BackendError:
                pass
        try:
            rows += P.dropbox_files(self._shell(serial, "dumpsys dropbox --file"), offset)
        except (api.DeviceNotFound, api.DeviceUnauthorized):
            raise
        except api.BackendError:
            pass
        return sorted(rows, key=lambda row: row.mtime or 0, reverse=True)

    def read_text(self, serial, path, max_bytes=2_000_000):
        if not isinstance(max_bytes, int) or max_bytes < 0:
            raise api.CommandFailed("读取上限必须是非负整数")
        if path.startswith("dropbox:"):
            match = re.fullmatch(r"dropbox:([\w.-]+)@(\d+)", path)
            if not match:
                raise api.CommandFailed("DropBox 记录标识无效")
            tag, millis = match[1], int(match[2])
            offset = self._device_timezone(serial)
            index = self._shell(serial, quote_args("dumpsys", "dropbox", "--file", tag))
            entry = next((row for row in P.dropbox_records(index, offset) if row["millis"] == millis), None)
            filter_args = entry["stamp"].split() if entry else []
            printed = self._shell(serial, quote_args("dumpsys", "dropbox", "--print", *filter_args, tag))
            selected = P.dropbox_content(printed, tag, millis, offset, index)
            if selected is None:
                raise api.CommandFailed("找不到指定的 DropBox 记录，记录可能已变化，请刷新列表")
            data = selected.encode("utf-8")
            size = len(data)
        else:
            # 只传上限+1 字节，避免把超大 tombstone 全部读入内存。
            size_out = self._shell(serial, quote_args("stat", "-L", "-c", "%s", "--", path))
            size = P.integer(size_out.strip())
            code, data, err = self._run(self._adb_args(serial, "exec-out", quote_args("head", "-c", max_bytes + 1, "--", path)))
            error = error_for(code, "", text(err), serial)
            if error:
                raise error
            if size is not None and len(data) > size:
                size = None  # /proc 等虚拟文件的 stat 大小可以是 0。
        truncated = (size is not None and size > max_bytes) or len(data) > max_bytes
        output = data[:max_bytes].decode("utf-8", errors="replace")
        suffix = f"原文件 {size} 字节" if size is not None else "原文件大小未知"
        return output + f"\n…（已截断，{suffix}）" if truncated else output

    def _dumpstate_pids(self, serial):
        result = self.shell(serial, "pidof dumpstate", timeout=1)
        return {int(pid) for pid in result.stdout.split() if pid.isdigit()} if result.exit_code == 0 else set()

    def _cancel_dumpstate(self, serial, before):
        # 只结束本次任务后新出现的 dumpstate，不触碰开始前已有的任务。
        for pid in self._dumpstate_pids(serial) - before:
            result = self.shell(serial, f'[ "$(cat /proc/{pid}/comm 2>/dev/null)" = dumpstate ] && kill -INT {pid}', timeout=1)
            if result.exit_code:
                continue  # 非 root 可能无权中止远端；本地 adb 仍已停止。
            deadline = time.monotonic() + .7
            while pid in self._dumpstate_pids(serial) and time.monotonic() < deadline:
                time.sleep(.05)
            if pid in self._dumpstate_pids(serial):
                self.shell(serial, f'[ "$(cat /proc/{pid}/comm 2>/dev/null)" = dumpstate ] && kill -KILL {pid}', timeout=1)

    def bugreport(self, serial, dest_dir, progress=None, cancel=None):
        with self._config_lock:
            lock = self._bugreport_locks.setdefault(serial, threading.Lock())
        while not lock.acquire(timeout=.1):
            if cancel and cancel.is_set():
                raise api.Cancelled("已取消")
        try:
            if cancel and cancel.is_set():
                raise api.Cancelled("已取消")
            before = self._dumpstate_pids(serial)
            if before:
                raise api.CommandFailed("设备已有 Bug 报告任务正在运行，请等待它结束后再试")
            try:
                return self._bugreport(serial, dest_dir, progress, cancel)
            except api.Cancelled:
                try:
                    self._cancel_dumpstate(serial, before)
                except api.BackendError:
                    pass
                raise
        finally:
            lock.release()

    def _bugreport(self, serial, dest_dir, progress=None, cancel=None):
        dest = Path(dest_dir)
        try:
            dest.mkdir(parents=True, exist_ok=True)
            before = {path.resolve(): path.stat().st_mtime_ns for path in dest.glob("bugreport-*.zip")}
            result = self._run_job(serial, ["bugreport", str(dest)], progress, cancel, "正在收集 Bug 报告…")
            path = P.bugreport_path(result.stdout + result.stderr)
            if path and Path(path).is_file():
                return str(Path(path))
            candidates = [path for path in dest.glob("bugreport-*.zip") if before.get(path.resolve()) != path.stat().st_mtime_ns]
            if candidates:
                return str(max(candidates, key=lambda path: path.stat().st_mtime_ns))
        except OSError as exc:
            raise api.CommandFailed("无法写入 Bug 报告目录", str(exc)) from exc
        raise api.CommandFailed("Bug 报告命令结束，但没有找到新生成的 ZIP 文件", detail(result.stdout + result.stderr))
