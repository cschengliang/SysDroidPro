"""只读采样工具：python tests/collect_emu.py；原始字节及命令记录一起保存。"""
from pathlib import Path
import json
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ADB = ROOT / "tool/platform-tools/adb.exe"
SERIAL = "emulator-5554"
DETAIL = "\n".join(f"echo =={name}; {cmd}" for name, cmd in [
    ("PROPS", "getprop"), ("UNAME", "uname -r"), ("KERNEL", "cat /proc/version"),
    ("SELINUX", "getenforce"), ("MEM", "head -n 3 /proc/meminfo"),
    ("WM", "wm size; wm density"), ("UPTIME", "cat /proc/uptime"),
    ("BATTERY", "dumpsys battery"), ("CURRENT", "cat /sys/class/power_supply/battery/current_now"),
    ("DF", "df -k /data /system 2>/dev/null")])
ROOT_STATE = "id -u; getprop ro.build.type; getprop ro.boot.veritymode; getprop partition.system.verified; mount | grep -E ' / | /system '"
SAMPLES = {
    "devices_l": ["devices", "-l"], "adb_version": ["version"],
    "getprop": ["shell", "getprop"], "getprop_Z": ["shell", "getprop -Z"],
    "battery": ["shell", "dumpsys battery"], "detail": ["shell", DETAIL],
    "root_state_nonroot": ["shell", ROOT_STATE], "id_u": ["shell", "id -u"],
    "service_list": ["shell", "service list"], "lshal": ["shell", "lshal --neat -itp"],
    "lshal_fallback": ["shell", "lshal -itp"], "lshal_help": ["shell", "lshal --help"],
    "ps_A": ["shell", "ps -A -o PID,PPID,USER,RSS,S,NAME"],
    "proc_status": ["shell", "cat /proc/1/status"], "proc_maps": ["shell", "cat /proc/1/maps"],
    "cmd_user_list_v": ["shell", "cmd user list -v"], "pm_list_users": ["shell", "pm list users"],
    "pm_list_packages_user0": ["shell", "pm list packages -f -U --show-versioncode --user 0"],
    "pm_list_packages_system": ["shell", "pm list packages -s --user 0"],
    "pm_list_packages_disabled": ["shell", "pm list packages -d --user 0"],
    "pm_list_packages_thirdparty": ["shell", "pm list packages -3 --user 0"],
    "apex_list": ["shell", "pm list packages --apex-only --show-versioncode -f"],
    "dumpsys_package_android": ["shell", "dumpsys package android"],
    "dumpsys_package_com.android.settings": ["shell", "dumpsys package com.android.settings"],
    "dumpsys_package_packages": ["shell", "dumpsys package packages"],
    "dumpsys_package_missing": ["shell", "dumpsys package no.such.package.dtb"],
    "ls_la_sdcard": ["shell", "ls -la /sdcard/"], "ls_la_root": ["shell", "ls -la /"],
    "ls_la_dev": ["shell", "ls -la /dev/"], "ls_la_data": ["shell", "ls -la /data/"],
    "tombstones": ["shell", "ls -l /data/tombstones/"], "anr": ["shell", "ls -l /data/anr/"],
    "dropbox": ["shell", "dumpsys dropbox"], "date_offset": ["shell", "date +%z"],
    "logcat": ["logcat", "-d", "-v", "threadtime", "-b", "main", "-t", "30"],
    "kernel_logcat": ["logcat", "-d", "-b", "main", "-b", "kernel", "-t", "3"],
    "dmesg_nonroot": ["shell", "dmesg"],
    "aidl_pid": ["shell", "dumpsys --pid android.hardware.power.IPower/default"],
    **{f"settings_list_{ns}": ["shell", f"settings list {ns}"] for ns in ("system", "secure", "global")},
}


def main():
    dest = ROOT / "tests/fixtures"
    dest.mkdir(exist_ok=True)
    manifest = {}
    for name, args in SAMPLES.items():
        cmd = [str(ADB), "-s", SERIAL, *args]
        result = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        (dest / f"{name}.emu36").write_bytes(result.stdout)
        if result.stderr:
            (dest / f"{name}.stderr.emu36").write_bytes(result.stderr)
        manifest[name] = {"command": cmd[1:], "exit_code": result.returncode}
        print(name, result.returncode, len(result.stdout), len(result.stderr))
    (dest / "commands.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
