"""独立 scrcpy 进程，使用选定的 adb 并恢复临时设备设置。"""
import os

from . import api
from ._base import error_for
from ._proc import ProcessStream


class MirrorMethods:
    def start_scrcpy(self, serial, options, on_output, on_exit):
        executable = self._tool("scrcpy")
        args = [executable, "-s", serial, f"--video-bit-rate={options.bit_rate_mbps}M"]
        for field, flag in (("max_size", "max-size"), ("max_fps", "max-fps"), ("record_path", "record"),
                            ("window_title", "window-title"), ("window_x", "window-x"), ("window_y", "window-y"),
                            ("window_width", "window-width"), ("window_height", "window-height")):
            value = getattr(options, field)
            if value is not None:
                args.append(f"--{flag}={value}")
        for field, flag in (("stay_awake", "stay-awake"), ("turn_screen_off", "turn-screen-off"),
                            ("show_touches", "show-touches"), ("always_on_top", "always-on-top")):
            if getattr(options, field):
                args.append("--" + flag)
        if not options.audio:
            args.append("--no-audio")
        environment = os.environ.copy()
        environment["ADB"] = self._tool("adb")
        restore = []
        for needed, namespace, key in ((options.stay_awake, "global", "stay_on_while_plugged_in"),
                                       (options.show_touches, "system", "show_touches")):
            if needed:
                values = self.list_settings(serial, namespace)
                restore.append((namespace, key, key in values, values.get(key)))
        process = self._popen(args, env=environment)

        def finish(code, out, err, stopped, error):
            failure = None if stopped else error or error_for(code, out, err, serial)
            for namespace, key, existed, value in restore:
                try:
                    if existed:
                        self.put_setting(serial, namespace, key, value)
                    else:
                        self.delete_setting(serial, namespace, key)
                except api.BackendError as exc:
                    failure = failure or exc
            on_exit(failure)

        return ProcessStream(process, on_output, finish, health=lambda: self._check_online(serial))
