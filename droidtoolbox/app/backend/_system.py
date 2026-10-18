"""属性、settings、Binder / HAL / init 服务和进程。"""
from dataclasses import replace
import re

from . import api, parsers as P
from ._base import detail, quote_args
from .models import HalInfo


def user_args(user):
    if user is None:
        return []
    if not isinstance(user, int) or user < 0:
        raise api.CommandFailed("用户 ID 必须是非负整数")
    return ["--user", str(user)]


def positive(value, label):
    if not isinstance(value, int) or value <= 0:
        raise api.CommandFailed(f"{label}必须是正整数")
    return value


class SystemMethods:
    def get_props(self, serial):
        return P.props(self._shell(serial, "getprop"))

    def get_prop_contexts(self, serial):
        return P.props(self._shell(serial, "getprop -Z"))

    def set_prop(self, serial, key, value):
        result = self.shell(serial, quote_args("setprop", key, value))
        output = result.stdout + result.stderr
        if result.exit_code or "failed to set property" in output.lower():
            if key.startswith("ro."):
                raise api.PermissionDenied(f"无法设置只读属性 {key}", detail(output))
            from ._base import error_for
            raise error_for(result.exit_code or 1, result.stdout, result.stderr, serial)

    def _settings_command(self, serial, namespace, action, user, *args):
        if namespace not in ("system", "secure", "global"):
            raise api.CommandFailed("不支持的系统设置命名空间")
        return self._shell(serial, quote_args("settings", *user_args(None if namespace == "global" else user), action, namespace, *args))

    def list_settings(self, serial, namespace, user=None):
        return P.settings(self._settings_command(serial, namespace, "list", user))

    def put_setting(self, serial, namespace, key, value, user=None):
        self._settings_command(serial, namespace, "put", user, key, value)

    def delete_setting(self, serial, namespace, key, user=None):
        self._settings_command(serial, namespace, "delete", user, key)

    def list_binder_services(self, serial):
        return P.binder_services(self._shell(serial, "service list"))

    def dumpsys(self, serial, service, args=None, timeout=60):
        return self._shell(serial, quote_args("dumpsys", service, *(args or [])), timeout)

    def list_init_services(self, serial):
        return P.init_services(self.get_props(serial))

    def control_init_service(self, serial, name, action):
        if action not in ("start", "stop", "restart"):
            raise api.CommandFailed("不支持的 init 服务操作")
        actions = ("stop", "start") if action == "restart" else (action,)
        for verb in actions:
            output = self._shell(serial, quote_args(verb, name))
            if re.search(r"failed to (?:start|stop)|could not", output, re.I):
                raise api.CommandFailed(f"无法{('启动' if verb == 'start' else '停止')} init 服务 {name}", detail(output))

    def _optional_shell(self, serial, script):
        try:
            return self._shell(serial, script)
        except (api.DeviceNotFound, api.DeviceUnauthorized):
            raise
        except api.BackendError:
            return ""

    def list_hals(self, serial):
        result = self.shell(serial, "lshal --neat -itp")
        rows = P.hidl_hals(result.stdout)
        if not rows and result.exit_code:
            rows = P.hidl_hals(self.shell(serial, "lshal -itp").stdout)
        aidl = [HalInfo(service.name, "aidl") for service in self.list_binder_services(serial) if service.name.startswith("android.hardware.")]
        if aidl:
            script = "\n".join(f"echo ==HAL{i}; {quote_args('dumpsys', '--pid', row.name)} 2>/dev/null || true" for i, row in enumerate(aidl))
            data = P.sections(self._optional_shell(serial, script))
            for i, row in enumerate(aidl):
                pid = next((int(line) for line in data.get(f"HAL{i}", "").splitlines() if line.strip().isdigit() and int(line) > 0), None)
                aidl[i] = replace(row, pid=pid)
        rows += aidl
        pids = sorted({row.pid for row in rows if row.pid is not None})
        names = P.process_names(self._optional_shell(serial, quote_args("ps", "-o", "PID,NAME", "-p", ",".join(map(str, pids))))) if pids else {}
        return sorted((replace(row, process=names.get(row.pid)) for row in rows), key=lambda row: row.name)

    def hal_debug(self, serial, name, kind):
        if kind == "hidl":
            return self._shell(serial, quote_args("lshal", "debug", name), 30)
        if kind != "aidl":
            raise api.CommandFailed("不支持的 HAL 类型")
        return self.dumpsys(serial, name, timeout=30)

    def list_processes(self, serial):
        return P.processes(self._shell(serial, "ps -A -o PID,PPID,USER,RSS,S,NAME"))

    def process_detail(self, serial, pid):
        positive(pid, "进程 ID")
        base = f"/proc/{pid}"
        script = f'''[ -d {base} ] || exit 44
echo ==STATUS; cat {base}/status 2>/dev/null || true
echo ==CMDLINE; cat {base}/cmdline 2>/dev/null | tr '\\0' ' '; echo
echo ==THREADS; for t in {base}/task/*; do [ -r "$t/comm" ] && printf '%s %s\\n' "${{t##*/}}" "$(cat "$t/comm" 2>/dev/null)"; done
echo ==LIBS; grep '\\.so' {base}/maps 2>/dev/null || true
echo ==FD; if ls {base}/fd >/dev/null 2>&1; then ls {base}/fd | wc -l; fi
true'''
        result = self.shell(serial, script)
        if result.exit_code == 44:
            raise api.CommandFailed(f"进程 {pid} 不存在（可能已退出）", detail(result.stderr), 44)
        if result.exit_code:
            from ._base import error_for
            raise error_for(result.exit_code, result.stdout, result.stderr, serial)
        return P.process_detail(pid, result.stdout)

    def dump_backtrace(self, serial, pid, timeout=30):
        positive(pid, "进程 ID")
        return self._shell(serial, quote_args("debuggerd", "-b", pid), timeout)

    def kill_process(self, serial, pid, signal=9):
        positive(pid, "进程 ID")
        if not isinstance(signal, int) or not 1 <= signal <= 64:
            raise api.CommandFailed("信号必须是 1 到 64 的整数")
        self._shell(serial, quote_args("kill", f"-{signal}", pid))

    def list_users(self, serial):
        result = self.shell(serial, "cmd user list -v")
        users = P.users(result.stdout)
        if result.exit_code or not users:
            users = P.users(self._shell(serial, "pm list users"))
        return users
