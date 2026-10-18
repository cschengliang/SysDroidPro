"""按 Android 用户查询应用、权限、组件并执行包操作。"""
from pathlib import Path
import re

from . import api, parsers as P
from ._base import detail, quote_args
from ._system import user_args


class AppMethods:
    def list_packages(self, serial, user=0):
        suffix = user_args(user)
        listing = self._shell(serial, quote_args("pm", "list", "packages", "-f", "-U", "--show-versioncode", *suffix))
        system = self._shell(serial, quote_args("pm", "list", "packages", "-s", *suffix))
        disabled = self._shell(serial, quote_args("pm", "list", "packages", "-d", *suffix))
        metadata = self._shell(serial, "dumpsys package packages")
        return P.packages(listing, system, disabled, metadata)

    def package_detail(self, serial, package, user=0):
        raw = self.dump_package(serial, package)
        parsed = P.package_detail(raw, package, user)
        if parsed is None:
            raise api.CommandFailed(f"用户 {user} 下找不到应用 {package}", detail(raw))
        listing = self._shell(serial, quote_args("pm", "list", "packages", "-f", "-U", "--show-versioncode", *user_args(user), package))
        system = f"package:{package}" if parsed.info.system else ""
        disabled = f"package:{package}" if not parsed.info.enabled else ""
        info = next((item for item in P.packages(listing, system, disabled, raw) if item.name == package), parsed.info)
        resolved = self._resolve(serial, package, user)
        return P.package_detail(raw, package, user, info, bool(resolved))

    def package_components(self, serial, package, user=0):
        raw = self.dump_package(serial, package)
        if P.package_detail(raw, package, user) is None:
            raise api.CommandFailed(f"用户 {user} 下找不到应用 {package}", detail(raw))
        return P.components(raw, package, user)

    def set_component_enabled(self, serial, component, enabled, user=0):
        output = self._shell(serial, quote_args("pm", "enable" if enabled else "disable", *user_args(user), component))
        if "new state:" not in output:
            raise api.CommandFailed("组件状态修改失败", detail(output))

    def dump_package(self, serial, package):
        return self._shell(serial, quote_args("dumpsys", "package", package))

    def install(self, serial, apk_paths, options, progress=None, cancel=None):
        if not apk_paths:
            raise api.CommandFailed("请先选择至少一个 APK 文件")
        flags = [flag for value, flag in ((options.replace, "-r"), (options.downgrade, "-d"), (options.grant_all, "-g"), (options.allow_test, "-t")) if value]
        args = ["install" if len(apk_paths) == 1 else "install-multiple", *flags, *user_args(options.user), *apk_paths]
        result = self._run_job(serial, args, progress, cancel, "正在推送并安装…", checked=False)
        output = result.stdout + result.stderr
        if result.exit_code or not re.search(r"(?m)^Success\s*$", output):
            raise api.CommandFailed(P.install_error(output), detail(output), result.exit_code)

    def uninstall(self, serial, package, keep_data=False, user=None):
        output = self._shell(serial, quote_args("pm", "uninstall", *(["-k"] if keep_data else []), *user_args(user), package))
        if "Success" not in output:
            message = "系统应用无法完全卸载，可以为当前用户卸载或停用" if "DELETE_FAILED_INTERNAL_ERROR" in output else "应用卸载失败"
            raise api.CommandFailed(message, detail(output))

    def clear_data(self, serial, package, user=0):
        output = self._shell(serial, quote_args("pm", "clear", *user_args(user), package))
        if "Success" not in output:
            raise api.CommandFailed("清除应用数据失败", detail(output))

    def force_stop(self, serial, package, user=0):
        self._shell(serial, quote_args("am", "force-stop", *user_args(user), package))

    def _resolve(self, serial, package, user):
        result = self.shell(serial, quote_args("cmd", "package", "resolve-activity", "--brief", *user_args(user), "-c", "android.intent.category.LAUNCHER", package))
        if result.exit_code:
            return None
        return next((line.strip() for line in reversed(result.stdout.splitlines()) if re.fullmatch(r"[\w.]+/[\w.$]+", line.strip())), None)

    def launch(self, serial, package, user=0):
        component = self._resolve(serial, package, user)
        if component is None:
            raise api.CommandFailed("该应用没有可启动的界面")
        output = self._shell(serial, quote_args("am", "start", *user_args(user), "-n", component))
        if re.search(r"(?im)^Error", output):
            raise api.CommandFailed("应用启动失败", detail(output))

    def set_enabled(self, serial, package, enabled, user=0):
        output = self._shell(serial, quote_args("pm", "enable" if enabled else "disable-user", *user_args(user), package))
        if "new state:" not in output:
            raise api.CommandFailed("应用状态修改失败", detail(output))

    def set_permission(self, serial, package, permission, granted, user=0):
        self._shell(serial, quote_args("pm", "grant" if granted else "revoke", *user_args(user), package, permission))

    def extract_apk(self, serial, package, dest_dir, progress=None):
        output = self._shell(serial, quote_args("pm", "path", package))
        remote_paths = [line[8:] for line in output.splitlines() if line.startswith("package:")]
        if not remote_paths:
            raise api.CommandFailed(f"找不到应用 {package} 的 APK", detail(output))
        dest = Path(dest_dir)
        paths = []
        try:
            dest.mkdir(parents=True, exist_ok=True)
            for remote in remote_paths:
                basename = remote.rsplit("/", 1)[-1]
                safe_package = package if re.fullmatch(r"[\w.]+", package) else "package"
                name = safe_package + (".apk" if not basename.startswith("split_") else "-" + basename)
                local = dest / name
                self._run_job(serial, ["pull", remote, str(local)], progress, message=f"正在导出 {basename}…")
                paths.append(str(local))
        except OSError as exc:
            raise api.CommandFailed("无法创建 APK 导出文件", str(exc)) from exc
        return paths
