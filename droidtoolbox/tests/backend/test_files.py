from pathlib import Path
import shlex
import threading

import pytest

from app.backend import api
from app.backend._base import BackendBase
from app.backend._crashes import CrashMethods
from app.backend._files import FileMethods, local_name, remove_stage
from app.backend._jobs import JobMethods
from tests.fakes import FakeProcess, FakeRunner, fake_backend, fixture


class Files(FileMethods, CrashMethods, JobMethods, BackendBase):
    pass


def test_list_dir_quotes_and_directory_links():
    runner = FakeRunner().add(("shell", "ls -la '/中文 a'\"'\"'b/'"), (0, fixture("ls_la_names.emu36"), ""))
    runner.add(lambda args: args[-1].startswith("for f in"), (0, "/中文 a'b/system link\n", ""))
    backend = fake_backend(Files, runner)
    rows = backend.list_dir("s", "/中文 a'b")
    assert rows[0].name == "system link" and rows[0].is_dir and rows[0].is_link
    backend._runner.add(lambda args: args[-1].startswith("mv --"), (0, "", ""))
    backend.rename("s", "/a'b", "/new 中文")
    assert shlex.split(runner.calls[-1][-1]) == ["mv", "--", "/a'b", "/new 中文"]


def test_push_cancel_removes_only_private_stage(tmp_path):
    source = tmp_path / "a 空.txt"
    source.write_bytes(b"hello")
    runner = FakeRunner().add(lambda args: args[-1].startswith(("mkdir -p --", "rm -rf --")), (0, "", ""))
    runner.add(lambda args: "du -sb --" in args[-1], (0, "2\tstage", ""))
    backend = fake_backend(Files, runner)
    cancel = threading.Event()
    count = []
    def progress(p):
        count.append(p)
        if len(count) == 2:
            cancel.set()
    with pytest.raises(api.Cancelled):
        backend.push("s", [str(source)], "/data/local/tmp/existing", progress, cancel)
    assert runner.spawned[0].terminated
    assert runner.calls[-1][-1].startswith("rm -rf -- /data/local/tmp/existing/.dtb-upload-")
    assert source.read_bytes() == b"hello"


def test_pull_cancel_preserves_existing_destination(tmp_path):
    existing = tmp_path / "a.txt"
    existing.write_bytes(b"original")
    runner = FakeRunner().add(lambda args: "du -sb --" in args[-1], (0, "100\t/a.txt", ""))
    runner.add(lambda args: args[-1].startswith("test -d"), (1, "", ""))
    backend = fake_backend(Files, runner)
    cancel = threading.Event()
    count = []
    def progress(p):
        count.append(p)
        if len(count) == 2:
            cancel.set()
    with pytest.raises(api.Cancelled):
        backend.pull("s", ["/a.txt"], str(tmp_path), progress, cancel)
    assert existing.read_bytes() == b"original" and not list(tmp_path.glob(".dtb-download-*"))
    assert runner.spawned[0].terminated


def test_pull_success_stages_and_returns_paths(tmp_path):
    runner = FakeRunner().add(lambda args: "du -sb --" in args[-1], (0, "5\t/a.txt", ""))
    runner.add(lambda args: args[-1].startswith("test -d"), (1, "", ""))
    original_popen = runner.popen
    def popen(args):
        process = original_popen(args)
        Path(args[-1]).write_bytes(b"hello")
        process.finish()
        return process
    runner.popen = popen
    backend = fake_backend(Files, runner)
    paths = backend.pull("s", ["/a.txt"], str(tmp_path))
    assert paths == [str(tmp_path / "a.txt")] and Path(paths[0]).read_bytes() == b"hello"
    assert not list(tmp_path.glob(".dtb-download-*"))


def test_read_text_limits_bytes_and_does_not_interpret_contents():
    runner = FakeRunner().add(lambda args: args[-1].startswith("stat -c"), (0, "100", ""))
    runner.add(lambda args: "exec-out" in args, (0, b"Permission denied in a log body", ""))
    backend = fake_backend(Files, runner)
    assert backend.read_text("s", "/log", 10).startswith("Permission\n…")
    assert "100 字节" in backend.read_text("s", "/log", 10)


def test_dropbox_read_and_bugreport_artifact(tmp_path):
    runner = FakeRunner().add(("shell", "date +%z"), (0, "+0000\n", ""))
    runner.add(lambda args: "dumpsys dropbox --file" in args[-1], (0, fixture("dropbox_file.emu36"), ""))
    runner.add(lambda args: "dumpsys dropbox --print" in args[-1], (0, fixture("dropbox_print.emu36"), ""))
    backend = fake_backend(Files, runner)
    from app.backend.parsers import dropbox_files, timezone_offset
    boot = next(row for row in dropbox_files(fixture("dropbox_file.emu36"), timezone_offset("+0000")) if row.name.startswith("SYSTEM_BOOT@"))
    assert "Build:" in backend.read_text("s", boot.path)
    process = FakeProcess()
    path = tmp_path / "bugreport-test.zip"
    path.write_bytes(b"zip")
    process.stdout.feed(f"Bug report copied to {path}\n")
    process.finish()
    runner.processes.append(process)
    assert backend.bugreport("s", str(tmp_path)) == str(path)


def test_cleanup_refuses_wrong_parent(tmp_path):
    stage = tmp_path / "user-data"
    stage.mkdir()
    with pytest.raises(api.CommandFailed):
        remove_stage(stage, tmp_path)
    assert stage.exists()
    with pytest.raises(api.CommandFailed):
        local_name("../escape")


def test_upload_explicit_unicode_target_and_directory_plan(tmp_path):
    source = tmp_path / "中文 a'b.txt"
    source.write_text("hello")
    folder = tmp_path / "中文目录"
    folder.mkdir()
    (folder / "中文子文件.txt").write_text("child")
    runner = FakeRunner().add(lambda args: True, (0, "", ""))
    original_popen = runner.popen
    def popen(args):
        process = original_popen(args)
        process.finish()
        return process
    runner.popen = popen
    backend = fake_backend(Files, runner)
    backend.push("s", [str(source), str(folder)], "/tmp/target")
    commands = [args for args in runner.calls if "push" in args]
    assert len(commands) == 2
    assert commands[0][-1].endswith("/中文 a'b.txt")
    assert commands[1][-1].endswith("/中文目录/中文子文件.txt")
