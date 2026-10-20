"""ADB 命令库：内置预设 + 用户命令（data/commands.json），参数占位、执行历史。不依赖设备，纯数据层。

命令内容支持 {名称:类型=默认值|选项} 参数占位；{{ 和 }} 表示字面量花括号。
"""
from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

from tbui._qt import QtCore, Signal

from .command_presets import PRESET_DATA
from .settings import DATA_DIR

PARAM_TYPES = ("text", "package", "service", "init", "pid", "prop", "file", "dir")
PLACEHOLDER = re.compile(
    r"\{(?P<name>[^\s{}:=|$]+)"
    r"(?::(?P<type>[^\s{}:=|$]+))?"
    r"(?:=(?P<default>[^{}|]*))?"
    r"(?P<choices>(?:\|[^{}|]*)*)\}"
)
_HISTORY_LIMIT = 50
_OUTPUT_LIMIT = 64 * 1024


@dataclass(frozen=True)
class Param:
    name: str
    type: str = "text"
    default: str = ""
    choices: tuple[str, ...] = ()


def _scan_placeholders(script: str) -> tuple[list[re.Match[str]], list[int]]:
    """同时扫描占位和未闭合括号，跳过字面量括号、shell 变量和转义括号。"""
    matches: list[re.Match[str]] = []
    unclosed: list[int] = []
    i = 0
    while i < len(script):
        if script.startswith(("{{", "}}", "\\{", "\\}"), i):
            i += 2
            continue
        if script.startswith("${", i):
            # shell 变量展开可嵌套，整个表达式都不参与参数解析。
            depth = 1
            i += 2
            while i < len(script) and depth:
                if script[i] == "\\":
                    i += 2
                    continue
                depth += (script[i] == "{") - (script[i] == "}")
                i += 1
            continue
        if script[i] == "{":
            match = PLACEHOLDER.match(script, i)
            if match:
                matches.append(match)
                i = match.end()
                continue
            unclosed.append(i)
        elif script[i] == "}" and unclosed:
            unclosed.pop()
        i += 1
    return matches, unclosed


def _param(match: re.Match[str]) -> Param:
    choices = tuple(match["choices"][1:].split("|")) if match["choices"] else ()
    default = match["default"]
    type_name = match["type"] or "text"
    return Param(match["name"], type_name if type_name in PARAM_TYPES else "text",
                 default if default is not None else (choices[0] if choices else ""), choices)


def _params(matches: list[re.Match[str]]) -> dict[str, Param]:
    params: dict[str, Param] = {}
    for match in matches:
        if match["name"] not in params:
            params[match["name"]] = _param(match)
    return params


@dataclass(frozen=True)
class Command:
    id: str
    name: str
    script: str                                  # 可多行，逐行执行
    kind: Literal["shell", "adb"] = "shell"      # shell：adb -s S shell <行>；adb：adb -s S <行>
    group: str = "我的命令"
    description: str = ""
    confirm: bool = False                        # 执行前确认
    timeout: int = 30                            # 秒
    persistent: bool = False                     # 持续运行，不限时，可停止（只取第一行）
    stop_on_error: bool = True
    builtin: bool = False
    root: bool = False                           # 执行前需要 adb root

    @property
    def params(self) -> list[Param]:
        return list(_params(_scan_placeholders(self.script)[0]).values())

    @property
    def placeholders(self) -> list[str]:
        return [param.name for param in self.params]

    def lines(self, values: dict[str, str] | None = None) -> list[str]:
        """展开占位后的非空命令行（# 开头为注释）。"""
        matches, _ = _scan_placeholders(self.script)
        params = _params(matches)
        values = values or {}
        parts: list[str] = []
        end = 0
        for match in matches:
            parts.append(self.script[end:match.start()].replace("{{", "{").replace("}}", "}"))
            name = match["name"]
            parts.append(values.get(name, params[name].default or match[0]))
            end = match.end()
        parts.append(self.script[end:].replace("{{", "{").replace("}}", "}"))
        text = "".join(parts)
        return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]

    def problems(self) -> list[str]:
        """返回保存前可展示给用户的中文问题描述。"""
        problems: list[str] = []
        matches, unclosed = _scan_placeholders(self.script)
        for match in matches:
            param = _param(match)
            if match["type"] and match["type"] not in PARAM_TYPES:
                problems.append(f"参数“{param.name}”使用了未知参数类型“{match['type']}”")
            if param.choices:
                if any(not choice.strip() for choice in param.choices):
                    problems.append(f"参数“{param.name}”的固定选项不能为空")
                if param.default not in param.choices:
                    problems.append(f"参数“{param.name}”的默认值不在固定选项中")
        if self.persistent and len(self.lines()) > 1:
            problems.append("持续运行命令只能有一条命令行")
        for pos in unclosed:
            line = self.script.count("\n", 0, pos) + 1
            problems.append(f"第 {line} 行有未闭合的 {{")
        return list(dict.fromkeys(problems))


@dataclass
class HistoryItem:
    command: str                                 # 命令名称
    lines: list[str]
    serial: str
    started: float
    results: list[tuple[str, int | None, str, str, float]] = field(default_factory=list)  # (行, 退出码, stdout, stderr, 耗时)
    kind: str = "shell"
    command_id: str = ""
    values: dict[str, str] = field(default_factory=dict)

    @property
    def exit_code(self) -> int | None:
        codes = [r[1] for r in self.results]
        if not codes:
            return None
        return next((c for c in codes if c not in (0, None)), codes[-1])


def _p(name, script, group, desc="", kind="shell", **kw) -> Command:
    return Command(id="builtin:" + name, name=name, script=script, kind=kind, group=group, description=desc, builtin=True, **kw)


def _preset(group, name, script, desc, flags, timeout=30) -> Command:
    return _p(name, script, group, desc, kind="adb" if "a" in flags else "shell", root="r" in flags,
              persistent="p" in flags, confirm="c" in flags, timeout=timeout)


PRESETS: list[Command] = [_preset(*row) for row in PRESET_DATA]


class CommandStore(QtCore.QObject):
    changed = Signal()

    def __init__(self, path: Path | None = None, history_path: Path | None = None):
        super().__init__()
        self.path = path or DATA_DIR / "commands.json"
        self.history_path = history_path if history_path is not None else self.path.with_name("command_history.json")
        self.user: list[Command] = []
        self.last_values: dict[str, str] = {}     # 占位符上次填写的值
        self.history: list[HistoryItem] = []
        self._load()
        self._load_history()

    # 读写 ------------------------------------------------------------
    def _load(self):
        try:
            data = json.loads(self.path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        self.user = [self._from_dict(d) for d in data.get("commands", []) if isinstance(d, dict) and d.get("name")]
        self.last_values = {str(k): str(v) for k, v in data.get("last_values", {}).items()}

    @staticmethod
    def _from_dict(d: dict) -> Command:
        fields = Command.__dataclass_fields__
        c = Command(**{k: v for k, v in d.items() if k in fields and k != "builtin"})
        return c if c.id else replace(c, id=uuid.uuid4().hex)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"version": 1, "commands": [{k: v for k, v in asdict(c).items() if k != "builtin"} for c in self.user],
                "last_values": self.last_values}
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.path)

    def _load_history(self):
        try:
            data = json.loads(self.history_path.read_text("utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(data, dict) or not isinstance(data.get("history"), list):
            return
        for row in data["history"]:
            try:
                item = self._history_from_dict(row)
            except (TypeError, ValueError, OverflowError):
                continue
            self.history.append(item)
            if len(self.history) == _HISTORY_LIMIT:
                break

    @staticmethod
    def _history_from_dict(row: dict) -> HistoryItem:
        if not isinstance(row, dict):
            raise ValueError("历史记录必须是对象")
        item = HistoryItem(**{k: v for k, v in row.items() if k in HistoryItem.__dataclass_fields__})
        if (not all(isinstance(s, str) for s in (item.command, item.serial, item.command_id))
                or item.kind not in ("shell", "adb")
                or not isinstance(item.lines, list) or not all(isinstance(s, str) for s in item.lines)
                or type(item.started) not in (int, float) or not math.isfinite(item.started)
                or not isinstance(item.values, dict)
                or not all(isinstance(k, str) and isinstance(v, str) for k, v in item.values.items())
                or not isinstance(item.results, list)):
            raise ValueError("历史记录字段格式错误")
        results = []
        for result in item.results:
            if not isinstance(result, list) or len(result) != 5:
                raise ValueError("历史结果必须包含五个字段")
            line, code, out, err, elapsed = result
            if (not all(isinstance(s, str) for s in (line, out, err))
                    or (code is not None and type(code) is not int)
                    or type(elapsed) not in (int, float) or not math.isfinite(elapsed)):
                raise ValueError("历史结果字段格式错误")
            results.append((line, code, out, err, elapsed))
        item.results = results
        return item

    def _save_history(self):
        def truncate(text: str) -> str:
            if len(text) <= _OUTPUT_LIMIT:
                return text
            suffix = f"\n…（已截断，原始 {len(text.encode('utf-8'))} 字节）"
            # 将说明也算在上限内，重启后再次保存不会重复截断说明。
            return text[:_OUTPUT_LIMIT - len(suffix)] + suffix

        rows = []
        for item in self.history:
            row = asdict(item)
            row["results"] = [[line, code, truncate(out), truncate(err), elapsed]
                              for line, code, out, err, elapsed in item.results]
            rows.append(row)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.history_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"version": 1, "history": rows}, ensure_ascii=False, indent=2), "utf-8")
        tmp.replace(self.history_path)

    # 查询 ------------------------------------------------------------
    def all(self) -> list[Command]:
        return self.user + PRESETS

    def get(self, cid: str) -> Command | None:
        return next((c for c in self.all() if c.id == cid), None)

    def groups(self) -> list[str]:
        """用户分组在前（按首次出现顺序），内置分组在后。"""
        out: list[str] = []
        for c in self.all():
            if c.group not in out:
                out.append(c.group)
        return out

    # 修改 ------------------------------------------------------------
    def new_id(self) -> str:
        return uuid.uuid4().hex

    def upsert(self, cmd: Command) -> Command:
        if cmd.builtin:
            raise ValueError("内置命令不能修改，请先“复制为我的命令”")
        for i, c in enumerate(self.user):
            if c.id == cmd.id:
                self.user[i] = cmd
                break
        else:
            self.user.append(cmd)
        self.save()
        self.changed.emit()
        return cmd

    def duplicate(self, cmd: Command) -> Command:
        copy = replace(cmd, id=self.new_id(), name=cmd.name + "（副本）" if not cmd.builtin else cmd.name,
                       group="我的命令" if cmd.builtin else cmd.group, builtin=False)
        return self.upsert(copy)

    def delete(self, cid: str):
        self.user = [c for c in self.user if c.id != cid]
        self.save()
        self.changed.emit()

    def move(self, cid: str, delta: int):
        i = next((k for k, c in enumerate(self.user) if c.id == cid), None)
        if i is None:
            return
        j = max(0, min(len(self.user) - 1, i + delta))
        self.user.insert(j, self.user.pop(i))
        self.save()
        self.changed.emit()

    def remember(self, values: dict[str, str]):
        self.last_values.update(values)
        self.save()

    def add_history(self, item: HistoryItem):
        self.history.insert(0, item)
        del self.history[_HISTORY_LIMIT:]
        self._save_history()

    def clear_history(self):
        self.history.clear()
        self._save_history()

    # 导入导出 --------------------------------------------------------
    def export_json(self, path: str, ids: list[str] | None = None):
        cmds = [c for c in self.user if ids is None or c.id in ids]
        Path(path).write_text(json.dumps({"version": 1, "exported": time.strftime("%Y-%m-%d %H:%M:%S"),
                                          "commands": [{k: v for k, v in asdict(c).items() if k != "builtin"} for c in cmds]},
                                         ensure_ascii=False, indent=2), "utf-8")
        return len(cmds)

    def import_json(self, path: str) -> int:
        """导入并追加；与现有命令同名同内容的跳过，id 冲突时换新 id。"""
        data = json.loads(Path(path).read_text("utf-8"))
        existing = {(c.name, c.script) for c in self.user}
        ids = {c.id for c in self.user}
        n = 0
        for d in data.get("commands", []):
            if not isinstance(d, dict) or not d.get("name") or not d.get("script"):
                continue
            c = self._from_dict(d)
            if (c.name, c.script) in existing:
                continue
            if c.id in ids or c.id.startswith("builtin:"):
                c = replace(c, id=self.new_id())
            self.user.append(c)
            ids.add(c.id)
            n += 1
        if n:
            self.save()
            self.changed.emit()
        return n
