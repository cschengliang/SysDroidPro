"""文件操作：私有临时目标承接传输，取消时不删除用户已有文件。"""
from dataclasses import replace
from pathlib import Path
import os
import posixpath
import shutil
import time
import uuid

from . import api, parsers as P
from ._base import quote_args
from .models import Progress


def local_size(path):
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file() and not item.is_symlink())


def remove_stage(stage, parent):
    # Windows 删除前验证最终绝对路径，不跟随指向其它位置的链接。
    if stage.resolve().parent != parent.resolve() or not stage.name.startswith(".dtb-download-") or stage.is_symlink():
        raise api.CommandFailed("临时下载目录不在预期位置，已停止清理")
    if stage.exists():
        shutil.rmtree(stage)


def local_name(name):
    if not name or name in (".", "..") or Path(name).name != name or (os.name == "nt" and (any(char in name for char in '\\/:*?"<>|') or name[-1] in ". ")):
        raise api.CommandFailed("设备文件名无法安全映射到本地文件系统")
    return name


class FileMethods:
    def _upload_plan(self, sources, cancel=None):
        directories, files, links = [], [], []
        for source in sources:
            if source.is_dir() and not source.is_symlink():
                directories.append(source.name)
                entries = source.rglob("*")
            else:
                entries = [source]
            for entry in entries:
                if cancel and cancel.is_set():
                    raise api.Cancelled("已取消")
                relative = posixpath.join(source.name, *entry.relative_to(source).parts) if source.is_dir() and not source.is_symlink() else source.name
                if entry.is_symlink():
                    links.append((entry, relative))
                elif entry.is_dir():
                    directories.append(relative)
                else:
                    files.append((entry, relative))
        return directories, files, links

    def list_dir(self, serial, path):
        path = posixpath.normpath("/" + path.lstrip("/"))
        rows = P.file_entries(self._shell(serial, quote_args("ls", "-la", path.rstrip("/") + "/")), path)
        links = [row.path for row in rows if row.is_link]
        if links:
            script = "for f in " + quote_args(*links) + '; do [ -d "$f" ] && printf "%s\\n" "$f"; done; true'
            directories = set(self._shell(serial, script).splitlines())
            rows = [replace(row, is_dir=True) if row.path in directories else row for row in rows]
        return sorted(rows, key=lambda row: (not row.is_dir, row.name.casefold()))

    def _remote_size(self, serial, paths):
        result = self.shell(serial, quote_args("du", "-sb", "--", *paths), timeout=2)
        if result.exit_code:
            return None
        sizes = [P.integer(line.split()[0]) for line in result.stdout.splitlines() if line.split()]
        return sum(sizes) if sizes and all(value is not None for value in sizes) else None

    def _measure(self, getter, total, started, message):
        try:
            size = getter()
        except (OSError, api.CommandTimeout, api.CommandFailed):
            size = None
        if size is None or not total:
            return Progress(None, message)
        percent = min(99, max(0, int(size * 100 / total)))
        speed = size / max(.001, time.monotonic() - started) / 1024 ** 2
        return Progress(percent, f"{message} {percent}% · {speed:.1f} MB/s")

    def push(self, serial, local_paths, remote_dir, progress=None, cancel=None):
        if not local_paths:
            return
        if cancel and cancel.is_set():
            raise api.Cancelled("已取消")
        sources = [Path(path) for path in local_paths]
        if any(not path.exists() for path in sources):
            raise api.CommandFailed("上传的本地文件不存在")
        if len({path.name for path in sources}) != len(sources):
            raise api.CommandFailed("上传文件的名称重复，请分开上传")
        stage = posixpath.join(remote_dir, ".dtb-upload-" + uuid.uuid4().hex)
        try:
            directories, files, links = self._upload_plan(sources, cancel)
            total, started = sum(path.stat().st_size for path, _ in files), time.monotonic()
        except OSError as exc:
            raise api.CommandFailed("无法读取本地上传文件", str(exc)) from exc
        self.mkdir(serial, stage)
        try:
            if directories:
                self._shell(serial, quote_args("mkdir", "-p", "--", *(posixpath.join(stage, path) for path in directories)))
            completed = 0
            for source, relative in files:
                target = posixpath.join(stage, relative)
                def measured():
                    current = self._remote_size(serial, [target])
                    return completed + current if current is not None else None
                # adb 37 Windows 把含中文的本地 basename 传给目录目标时会截短；
                # 每个文件显式指定完整目标，目录由 Python 遍历，避免原生递归卡住。
                self._run_job(serial, ["push", str(source), target], progress, cancel, "正在上传",
                              measure=lambda: self._measure(measured, total, started, "正在上传"))
                completed += source.stat().st_size
            for source, relative in links:
                self._shell(serial, quote_args("ln", "-s", "--", str(source.readlink()), posixpath.join(stage, relative)))
            if cancel and cancel.is_set():
                raise api.Cancelled("已取消")
            for source in sources:
                src, dst = posixpath.join(stage, source.name), posixpath.join(remote_dir, source.name)
                src_q, dst_q = quote_args(src), quote_args(dst)
                script = f'if [ -d {src_q} ] && [ -d {dst_q} ]; then cp -a {quote_args(src + "/.")} {quote_args(dst + "/")}; else mv -f -- {src_q} {dst_q}; fi'
                self._shell(serial, script, timeout=120)
            if progress:
                progress(Progress(100, "上传完成"))
        except OSError as exc:
            raise api.CommandFailed("无法读取本地上传文件", str(exc)) from exc
        finally:
            try:
                self.delete(serial, [stage])
            except api.BackendError:
                if not (cancel and cancel.is_set()):
                    raise

    def pull(self, serial, remote_paths, local_dir, progress=None, cancel=None):
        if not remote_paths:
            return []
        if cancel and cancel.is_set():
            raise api.Cancelled("已取消")
        names = [local_name(posixpath.basename(path.rstrip("/"))) for path in remote_paths]
        if len(set(names)) != len(names):
            raise api.CommandFailed("下载文件的名称无效或重复，请分开下载")
        parent = Path(local_dir)
        stage = parent / (".dtb-download-" + uuid.uuid4().hex)
        try:
            parent.mkdir(parents=True, exist_ok=True)
            stage.mkdir()
            total, started = self._remote_size(serial, remote_paths), time.monotonic()
            plan = []
            def walk(remote, local):
                if cancel and cancel.is_set():
                    raise api.Cancelled("已取消")
                if self.shell(serial, quote_args("test", "-d", remote)).exit_code == 0:
                    local.mkdir(parents=True, exist_ok=True)
                    for entry in self.list_dir(serial, remote):
                        if entry.is_link and entry.is_dir:
                            raise api.CommandFailed("下载目录中有目录符号链接，请单独下载该链接目标，避免循环")
                        walk(entry.path, local / local_name(entry.name))
                else:
                    plan.append((remote, local))
            for remote, name in zip(remote_paths, names):
                walk(remote.rstrip("/"), stage / name)
            for remote, local in plan:
                local.parent.mkdir(parents=True, exist_ok=True)
                self._run_job(serial, ["pull", remote, str(local)], progress, cancel, "正在下载",
                              measure=lambda: self._measure(lambda: local_size(stage), total, started, "正在下载"))
            paths = []
            for name in names:
                source, target = stage / name, parent / name
                if not source.exists():
                    raise api.CommandFailed("下载命令结束，但没有生成预期的本地文件")
                if source.is_dir() and target.is_dir():
                    shutil.copytree(source, target, dirs_exist_ok=True)
                else:
                    source.replace(target)
                paths.append(str(target))
            if progress:
                progress(Progress(100, "下载完成"))
            return paths
        except OSError as exc:
            raise api.CommandFailed("无法写入本地下载目录", str(exc)) from exc
        finally:
            if stage.exists():
                remove_stage(stage, parent)

    def delete(self, serial, paths):
        if paths:
            self._shell(serial, quote_args("rm", "-rf", "--", *paths))

    def mkdir(self, serial, path):
        self._shell(serial, quote_args("mkdir", "-p", "--", path))

    def rename(self, serial, src, dst):
        self._shell(serial, quote_args("mv", "--", src, dst))
