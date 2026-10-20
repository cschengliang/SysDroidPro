"""命令参数、JSON 兼容性和执行历史；所有持久化均使用临时目录。"""
from __future__ import annotations

import json
from dataclasses import FrozenInstanceError, asdict

import pytest

from app.core.commands import PARAM_TYPES, PRESETS, Command, CommandStore, HistoryItem, Param


def command(script: str, **kwargs) -> Command:
    return Command(id="test-command", name="测试命令", script=script, **kwargs)


def history(index: int = 0, **kwargs) -> HistoryItem:
    return HistoryItem(f"命令 {index}", [f"echo {index}"], "test-serial", 1_700_000_000.0 + index, **kwargs)


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_param_is_frozen_and_has_defaults():
    param = Param("名称")
    assert (param.type, param.default, param.choices) == ("text", "", ())
    with pytest.raises(FrozenInstanceError):
        param.name = "另一个名称"


def test_param_types():
    assert PARAM_TYPES == ("text", "package", "service", "init", "pid", "prop", "file", "dir")


@pytest.mark.parametrize("type_name", PARAM_TYPES)
def test_parse_all_types(type_name):
    cmd = command(f"echo {{参数:{type_name}}}")
    assert cmd.params == [Param("参数", type_name)]
    assert cmd.placeholders == ["参数"]
    assert cmd.problems() == []


@pytest.mark.parametrize(("script", "expected"), [
    ("echo {名称}", Param("名称")),
    ("echo {包名:package=com.example.app}", Param("包名", "package", "com.example.app")),
    ("echo {说明=含 空格 的默认值}", Param("说明", default="含 空格 的默认值")),
    ("echo {路径:file=C:/Program Files/a.apk}", Param("路径", "file", "C:/Program Files/a.apk")),
    ("echo {说明=  保留空格  }", Param("说明", default="  保留空格  ")),
    ("echo {表达式=a=b:c$var}", Param("表达式", default="a=b:c$var")),
    ("echo {名称=}", Param("名称")),
    ("logcat *:{级别|V|D|I|W|E}", Param("级别", default="V", choices=("V", "D", "I", "W", "E"))),
    ("logcat *:{级别=I|V|D|I|W|E}", Param("级别", default="I", choices=("V", "D", "I", "W", "E"))),
    ("echo {服务:service=audio|media.audio_flinger|audio}",
     Param("服务", "service", "audio", ("media.audio_flinger", "audio"))),
    ("echo {选项|选项 一|选项 二}", Param("选项", default="选项 一", choices=("选项 一", "选项 二"))),
    ("echo {属性:prop|ro.build.type|ro.debuggable}",
     Param("属性", "prop", "ro.build.type", ("ro.build.type", "ro.debuggable"))),
])
def test_parse_definitions(script, expected):
    cmd = command(script)
    assert cmd.params == [expected]
    assert cmd.problems() == []


def test_first_definition_wins_and_names_keep_first_occurrence_order():
    cmd = command("echo {包:package=com.first|com.first|com.second} {其他=first} {包} "
                  "{包:text=com.second|com.second} {其他=second} {最后}")
    assert cmd.params == [Param("包", "package", "com.first", ("com.first", "com.second")),
                          Param("其他", default="first"), Param("最后")]
    assert cmd.placeholders == ["包", "其他", "最后"]
    assert cmd.lines() == ["echo com.first first com.first com.first first {最后}"]
    assert cmd.lines({"包": "com.user", "其他": "value", "最后": "end"}) == [
        "echo com.user value com.user com.user value end"]


def test_later_definition_does_not_supply_a_missing_default():
    cmd = command("echo {名称} {名称:package=com.example}")
    assert cmd.params == [Param("名称")]
    assert cmd.lines() == [cmd.script]


@pytest.mark.parametrize("script", [
    "echo {{名称}}", "echo {{名称:package=default}}", "echo {{ 名称 }}",
    "echo ${VAR}", "echo ${VAR:-${OTHER}}", "echo ${VAR:-{fallback}}",
    "awk '{print $1}'", "awk '{$1}'", 'echo "$(cat $z/type)"',
    "echo {bad name}", "echo {bad\tname}", "echo {bad\nname}", "echo {$VAR}",
    "echo {bad$name}", "echo {bad:type:extra}", "echo {bad:}", "echo {}",
])
def test_shell_syntax_and_invalid_names_are_not_parameters(script):
    cmd = command(script)
    assert cmd.params == []
    assert cmd.placeholders == []
    assert cmd.problems() == []


def test_expand_defaults_choices_and_missing_values():
    cmd = command("echo {名称} {包:package=com.example} {级别|I|W} {数字:pid}")
    assert cmd.lines() == ["echo {名称} com.example I {数字:pid}"]
    assert cmd.lines({"名称": "填入值", "级别": "W", "unused": "ignored"}) == [
        "echo 填入值 com.example W {数字:pid}"]
    assert cmd.lines({"名称": "", "包": "", "数字": "42"}) == ["echo   I 42"]


def test_lines_filter_comments_and_blank_lines_and_restore_literal_braces():
    cmd = command("\n # {注释}\n\t\n  echo {{名称}} {名称=hello world}  \n"
                  "  echo ${VAR} '{print $1}' $(cat $z/type) {{ }}  \n")
    assert cmd.lines() == ["echo {名称} hello world", "echo ${VAR} '{print $1}' $(cat $z/type) { }"]


def test_literal_braces_around_parameter_and_inside_values():
    assert command("echo {{{名称}}}").lines({"名称": "value"}) == ["echo {value}"]
    assert command("echo {名称}").lines({"名称": "{{raw}} {other}"}) == ["echo {{raw}} {other}"]


def test_unknown_type_falls_back_to_text_and_is_reported():
    cmd = command("echo {路径:unknown=/tmp}")
    assert cmd.params == [Param("路径", default="/tmp")]
    assert cmd.lines() == ["echo /tmp"]
    assert len(cmd.problems()) == 1
    assert all(s in cmd.problems()[0] for s in ("路径", "未知", "unknown"))


@pytest.mark.parametrize("script", ["echo {x|}", "echo {x|a||b}", "echo {x|a|}", "echo {x|   }"])
def test_empty_choices_are_reported(script):
    assert any("固定选项" in p and "空" in p for p in command(script).problems())


@pytest.mark.parametrize("script", ["echo {x=Z|A|B}", "echo {x:text=|A|B}"])
def test_default_outside_choices_is_reported(script):
    assert any("默认值不在固定选项中" in p for p in command(script).problems())


@pytest.mark.parametrize("script", ["echo {名称", "echo {", "echo {名称\necho {另一个}"])
def test_unclosed_braces_are_reported(script):
    assert any("未闭合" in p for p in command(script).problems())


@pytest.mark.parametrize("script", ["echo {{", "echo }}", "echo ${VAR", r"grep 'Task\{'", "awk '{print $1}'"])
def test_literal_shell_and_regex_braces_are_not_reported(script):
    assert command(script).problems() == []


def test_problems_reports_all_categories():
    cmd = command("echo {x:unknown=Z|A|}\necho {open", persistent=True)
    problems = cmd.problems()
    assert len(problems) == 5
    assert any("未知" in p for p in problems)
    assert any("持续运行" in p for p in problems)
    assert any("未闭合" in p for p in problems)
    assert any("固定选项不能为空" in p for p in problems)
    assert any("默认值不在" in p for p in problems)


def test_persistent_validation_counts_only_executable_lines():
    assert command("# 注释\n\n top -d 2\n# 结尾", persistent=True).problems() == []
    assert command("echo one\necho two").problems() == []
    assert any("一条命令行" in p for p in command("echo one\necho two", persistent=True).problems())


@pytest.mark.parametrize("preset", PRESETS, ids=lambda c: c.id)
def test_presets_have_no_problems(preset):
    assert preset.problems() == []


@pytest.mark.parametrize("root", [False, True])
def test_root_command_save_and_reload(tmp_path, root):
    path = tmp_path / "nested" / "commands.json"
    store = CommandStore(path)
    cmd = command("id", root=root)
    store.upsert(cmd)
    assert read_json(path)["commands"][0]["root"] is root
    assert CommandStore(path).get(cmd.id) == cmd
    assert not path.with_suffix(".tmp").exists()


def test_old_command_file_defaults_to_non_root(tmp_path):
    path = tmp_path / "commands.json"
    write_json(path, {"version": 1, "commands": [{"id": "old", "name": "旧命令", "script": "id"}],
                      "last_values": {"名称": "previous"}})
    store = CommandStore(path)
    assert store.get("old").root is False
    assert store.last_values == {"名称": "previous"}
    store.save()
    assert read_json(path)["commands"][0]["root"] is False


def test_export_and_import_keep_root(tmp_path):
    source = CommandStore(tmp_path / "source.json")
    source.upsert(command("id", root=True))
    source.upsert(Command("other", "另一个命令", "echo hello"))
    exported = tmp_path / "export.json"
    assert source.export_json(str(exported)) == 2
    assert [c["root"] for c in read_json(exported)["commands"]] == [True, False]
    target_path = tmp_path / "target.json"
    target = CommandStore(target_path)
    assert target.import_json(str(exported)) == 2
    assert target.user == source.user
    assert CommandStore(target_path).user == source.user


def test_import_old_json_defaults_to_non_root(tmp_path):
    exported = tmp_path / "old-export.json"
    write_json(exported, {"commands": [{"id": "old", "name": "旧命令", "script": "id"}]})
    store = CommandStore(tmp_path / "commands.json")
    assert store.import_json(str(exported)) == 1
    assert store.get("old").root is False


def test_history_defaults_and_mutable_fields_are_independent():
    first, second = history(), history()
    assert (first.kind, first.command_id, first.values, first.results) == ("shell", "", {}, [])
    first.values["包"] = "com.example"
    first.results.append(("id", 0, "ok", "", 0.1))
    assert second.values == {}
    assert second.results == []


def test_history_round_trip_and_result_tuples(tmp_path):
    path = tmp_path / "commands.json"
    store = CommandStore(path)
    item = history(kind="adb", command_id="test-command", values={"目录": "C:/含 空格"},
                   results=[("first", 0, "stdout\n", "", 0.25), ("second", 1, "", "stderr\n", 1.5),
                            ("third", None, "", "stopped", 0.5)])
    store.add_history(item)
    assert store.history_path == tmp_path / "command_history.json"
    data = read_json(store.history_path)
    assert data["history"][0]["results"][0] == ["first", 0, "stdout\n", "", 0.25]
    restored = CommandStore(path).history
    assert restored == [item]
    assert all(isinstance(result, tuple) for result in restored[0].results)
    assert restored[0].exit_code == 1
    assert not store.history_path.with_suffix(".tmp").exists()
    assert not path.exists()


def test_custom_history_path(tmp_path):
    path = tmp_path / "commands.json"
    history_path = tmp_path / "separate" / "custom.json"
    store = CommandStore(path, history_path)
    store.add_history(history())
    assert CommandStore(path, history_path=history_path).history == store.history
    assert not (tmp_path / "command_history.json").exists()


def test_old_history_fields_get_defaults(tmp_path):
    path = tmp_path / "commands.json"
    write_json(tmp_path / "command_history.json", {"history": [
        {"command": "旧记录", "lines": ["id"], "serial": "test", "started": 1.0,
         "results": [["id", 0, "ok", "", 0.1]]}]})
    item = CommandStore(path).history[0]
    assert (item.kind, item.command_id, item.values) == ("shell", "", {})
    assert item.results == [("id", 0, "ok", "", 0.1)]


@pytest.mark.parametrize(("stdout", "stderr"), [
    ("a" * 65536, "b" * 65537),
    ("中" * 70000, "错误" * 40000),
    ("a" * 65537, "short"),
], ids=["boundary", "utf8", "stdout-only"])
def test_history_output_limits_do_not_modify_memory(tmp_path, stdout, stderr):
    path = tmp_path / "commands.json"
    store = CommandStore(path)
    item = history(results=[("line", 0, stdout, stderr, 0.1), ("second", 0, "ok", "", 0.2)])
    store.add_history(item)
    assert store.history[0] is item
    assert item.results[0][2:4] == (stdout, stderr)
    restored = CommandStore(path)
    saved = restored.history[0].results[0]
    for original, truncated in zip((stdout, stderr), saved[2:4]):
        assert len(truncated) <= 65536
        if len(original) <= 65536:
            assert truncated == original
        else:
            suffix = f"\n…（已截断，原始 {len(original.encode('utf-8'))} 字节）"
            assert truncated.endswith(suffix)
            assert truncated == original[:65536 - len(suffix)] + suffix
    assert restored.history[0].results[1] == item.results[1]
    restored.add_history(history(1))
    assert CommandStore(path).history[1].results[0] == saved


def test_history_keeps_fifty_newest_items_on_disk_and_in_memory(tmp_path):
    path = tmp_path / "commands.json"
    store = CommandStore(path)
    for index in range(55):
        store.add_history(history(index))
    expected_names = [f"命令 {index}" for index in range(54, 4, -1)]
    assert [item.command for item in store.history] == expected_names
    assert [item.command for item in CommandStore(path).history] == expected_names
    assert len(read_json(store.history_path)["history"]) == 50


def test_loading_oversized_history_respects_limit(tmp_path):
    write_json(tmp_path / "command_history.json", {"history": [asdict(history(i)) for i in range(55)]})
    store = CommandStore(tmp_path / "commands.json")
    assert len(store.history) == 50
    assert [item.command for item in store.history] == [f"命令 {i}" for i in range(50)]


@pytest.mark.parametrize("contents", [
    "{broken", "null", "[]", "1", '"text"', "{}", '{"history": null}', '{"history": {}}',
    '{"history": [null, [], 42, {}]}',
])
def test_corrupt_or_invalid_history_is_ignored(tmp_path, contents):
    path = tmp_path / "commands.json"
    (tmp_path / "command_history.json").write_text(contents, encoding="utf-8")
    store = CommandStore(path)
    assert store.history == []
    store.add_history(history())
    assert CommandStore(path).history == store.history


@pytest.mark.parametrize("override", [
    {"command": None}, {"serial": []}, {"started": "now"}, {"started": True}, {"started": float("nan")},
    {"started": 10 ** 1000},
    {"lines": "id"}, {"lines": [None]}, {"kind": "invalid"}, {"command_id": 1},
    {"values": []}, {"values": {"name": 1}}, {"results": None}, {"results": [{}]},
    {"results": [["id", 0, "ok", "", 0, "extra"]]}, {"results": [["id", "0", "ok", "", 0]]},
    {"results": [["id", 0, None, "", 0]]}, {"results": [["id", 0, "", [], 0]]},
    {"results": [["id", 0, "", "", "fast"]]}, {"results": [["id", 0, "", "", float("inf")]]},
])
def test_invalid_history_records_are_skipped(tmp_path, override):
    valid = asdict(history())
    write_json(tmp_path / "command_history.json", {"history": [dict(valid, **override), valid]})
    assert CommandStore(tmp_path / "commands.json").history == [history()]


def test_invalid_utf8_and_unreadable_history_are_ignored(tmp_path):
    path = tmp_path / "commands.json"
    history_path = tmp_path / "command_history.json"
    history_path.write_bytes(b"\xff\xfe")
    assert CommandStore(path).history == []
    assert CommandStore(path, tmp_path).history == []  # 目录不能作为 JSON 文件读取。


def test_clear_history_saves_empty_history_without_changing_commands(tmp_path):
    path = tmp_path / "commands.json"
    store = CommandStore(path)
    store.upsert(command("id", root=True))
    store.remember({"包": "com.example"})
    original_commands = path.read_bytes()
    store.add_history(history())
    store.clear_history()
    assert store.history == []
    assert read_json(store.history_path)["history"] == []
    assert CommandStore(path).history == []
    assert path.read_bytes() == original_commands
    store.clear_history()
    assert CommandStore(path).history == []
