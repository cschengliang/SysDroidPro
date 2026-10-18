"""ADB 命令库：内置预设 + 用户命令（data/commands.json），参数占位、执行历史。不依赖设备，纯数据层。

命令内容中的 {名称} 是参数占位，执行时弹框填写；{{ 和 }} 表示字面量花括号。
"""
from __future__ import annotations

import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Literal

from tbui._qt import QtCore, Signal

from .settings import DATA_DIR

PLACEHOLDER = re.compile(r"(?<!\{)\{([^{}\s][^{}]*)\}(?!\})")


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

    @property
    def placeholders(self) -> list[str]:
        seen: list[str] = []
        for m in PLACEHOLDER.finditer(self.script):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        return seen

    def lines(self, values: dict[str, str] | None = None) -> list[str]:
        """展开占位后的非空命令行（# 开头为注释）。"""
        text = PLACEHOLDER.sub(lambda m: (values or {}).get(m.group(1), m.group(0)), self.script)
        text = text.replace("{{", "{").replace("}}", "}")
        return [l.strip() for l in text.splitlines() if l.strip() and not l.strip().startswith("#")]


@dataclass
class HistoryItem:
    command: str                                 # 命令名称
    lines: list[str]
    serial: str
    started: float
    results: list[tuple[str, int | None, str, str, float]] = field(default_factory=list)  # (行, 退出码, stdout, stderr, 耗时)

    @property
    def exit_code(self) -> int | None:
        codes = [r[1] for r in self.results]
        if not codes:
            return None
        return next((c for c in codes if c not in (0, None)), codes[-1])


def _p(name, script, group, desc="", kind="shell", **kw) -> Command:
    return Command(id="builtin:" + name, name=name, script=script, kind=kind, group=group, description=desc, builtin=True, **kw)


PRESETS: list[Command] = [
    _p("当前焦点窗口", "dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", "系统信息", "当前获得焦点的窗口与 Activity"),
    _p("栈顶 Activity", "dumpsys activity top | head -n 60", "系统信息"),
    _p("Activity 栈", "dumpsys activity activities | grep -E 'Task\\{|ActivityRecord'", "系统信息"),
    _p("内核启动参数", "cat /proc/cmdline", "系统信息"),
    _p("内核版本", "cat /proc/version", "系统信息"),
    _p("挂载情况", "mount | grep -E ' /(system|vendor|product|odm|system_ext|data) '", "系统信息"),
    _p("分区占用", "df -h", "系统信息"),
    _p("温度节点", 'for z in /sys/class/thermal/thermal_zone*; do echo "$(cat $z/type)=$(cat $z/temp)"; done', "系统信息"),
    _p("CPU 频率", "cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq", "系统信息"),
    _p("Binder 服务列表", "service list", "服务与 HAL"),
    _p("检查服务是否存在", "service check {服务名}", "服务与 HAL"),
    _p("HIDL HAL 列表", "lshal", "服务与 HAL"),
    _p("VINTF 清单", "cat /vendor/etc/vintf/manifest.xml", "服务与 HAL"),
    _p("init 服务状态", "getprop | grep init.svc.", "服务与 HAL"),
    _p("重启 init 服务", "stop {服务名}\nstart {服务名}", "服务与 HAL", "需要 root", confirm=True),
    _p("SELinux 状态", "getenforce", "SELinux"),
    _p("内核 avc 拒绝", "dmesg | grep 'avc:'", "SELinux", "需要 root"),
    _p("logcat avc 拒绝", "logcat -d -b all | grep 'avc:'", "SELinux"),
    _p("属性的 SELinux 上下文", "getprop -Z {属性名}", "SELinux"),
    _p("进程的安全上下文", "ps -AZ | grep {进程名}", "SELinux"),
    _p("文件的安全上下文", "ls -Z {路径}", "SELinux"),
    _p("实时 top", "top -d 2 -m 15", "调试", "持续运行，点“停止”结束", persistent=True),
    _p("打印进程调用栈", "debuggerd -b {PID}", "调试", "需要 root"),
    _p("tombstone 列表", "ls -l /data/tombstones", "调试", "需要 root"),
    _p("进程内存", "dumpsys meminfo {包名或PID}", "调试"),
    _p("实时内核日志", "dmesg -w", "调试", "需要 root；持续运行", persistent=True),
    _p("重启到 Bootloader", "reboot bootloader", "adb", kind="adb", confirm=True),
    _p("关闭 verity", "disable-verity", "adb", "需要 root，重启生效", kind="adb", confirm=True),
    _p("拉取 tombstones", "pull /data/tombstones {本地目录}", "adb", "需要 root", kind="adb", timeout=120),
]


class CommandStore(QtCore.QObject):
    changed = Signal()

    def __init__(self, path: Path | None = None):
        super().__init__()
        self.path = path or DATA_DIR / "commands.json"
        self.user: list[Command] = []
        self.last_values: dict[str, str] = {}     # 占位符上次填写的值
        self.history: list[HistoryItem] = []
        self._load()

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
        del self.history[50:]

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
