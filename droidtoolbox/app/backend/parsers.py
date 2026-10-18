"""Android 命令输出的纯解析器；缺失的可选字段保持 None。"""
from __future__ import annotations

import re

from . import models as M


def integer(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sections(output):
    result, key = {}, None
    for line in output.splitlines():
        if re.fullmatch(r"==[A-Z_]+", line):
            key = line[2:]
            result[key] = []
        elif key:
            result[key].append(line)
    return {k: "\n".join(v) for k, v in result.items()}


def props(output):
    entries = re.finditer(r"(?m)^\[([^\]\r\n]+)\]: \[", output)
    entries = list(entries)
    result = {}
    for i, match in enumerate(entries):
        end = entries[i + 1].start() if i + 1 < len(entries) else len(output)
        value = output[match.end():end].rstrip("\r\n")
        if value.endswith("]"):
            value = value[:-1]
        result[match[1]] = value.replace("\r\n", "\n")
    return result


def devices(output, fastboot=False):
    result = []
    states = {"device", "offline", "unauthorized", "authorizing", "connecting", "recovery", "fastboot", "sideload", "bootloader"}
    for line in output.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[1] not in states or fields[0] == "*":
            continue
        serial, state = fields[:2]
        transport = "emulator" if serial.startswith("emulator-") else "wifi" if ":" in serial or "._adb-tls-" in serial else "usb"
        values = dict(item.split(":", 1) for item in fields[2:] if ":" in item)
        model = values.get("model")
        result.append(M.Device(serial, "fastboot" if fastboot else state, transport,
                               model.replace("_", " ") if model else None, product=values.get("device") or values.get("product")))
    return result


def battery(output, current=""):
    values = {}
    for line in output.splitlines():
        if ":" in line:
            key, value = line.strip().split(":", 1)
            values[key] = value.strip()
    level = integer(values.get("level"))
    status = integer(values.get("status"))
    temp, voltage, amps = number(values.get("temperature")), number(values.get("voltage")), integer(current.strip())
    if not any(key in values for key in ("level", "status", "temperature", "voltage")):
        return None
    if amps is not None:
        amps = int(abs(amps) / 1000) * (1 if status == 2 else -1 if status == 3 else (1 if amps >= 0 else -1))
    return M.BatteryInfo(level if level is not None and 0 <= level <= 100 else None,
        {1: "未知", 2: "充电中", 3: "放电中", 4: "未充电", 5: "已充满"}.get(status),
        next((label for key, label in (("AC powered", "AC"), ("USB powered", "USB"), ("Wireless powered", "无线")) if values.get(key) == "true"), None),
        temp / 10 if temp is not None else None, voltage / 1000 if voltage is not None else None, amps,
        {2: "良好", 3: "过热", 4: "损坏", 5: "过压", 7: "过冷"}.get(integer(values.get("health"))))


def device_detail(serial, output):
    data = sections(output)
    p = props(data.get("PROPS", ""))
    get = lambda key: p.get(key) or None
    wm = data.get("WM", "")
    sizes = re.findall(r"(Physical|Override) size: (\d+)x(\d+)", wm)
    resolution = None
    if sizes:
        _, width, height = sizes[-1]
        resolution = f"{width} × {height}" + ("（覆盖）" if sizes[-1][0] == "Override" else "")
    densities = re.findall(r"(?:Physical|Override) density: (\d+)", wm)
    memory = dict(re.findall(r"(MemTotal|MemAvailable):\s+(\d+)", data.get("MEM", "")))
    uptime = number(data.get("UPTIME", "").split(" ")[0])
    storage = []
    for line in data.get("DF", "").splitlines():
        cells = line.split()
        if len(cells) >= 6 and cells[1].isdigit() and cells[2].isdigit():
            storage.append(M.StorageInfo(cells[-1], int(cells[1]) * 1024, int(cells[2]) * 1024))
    return M.DeviceDetail(serial=serial, model=get("ro.product.model"), manufacturer=get("ro.product.manufacturer"),
        brand=get("ro.product.brand"), device=get("ro.product.device"), android=get("ro.build.version.release"),
        api=integer(get("ro.build.version.sdk")), build_id=get("ro.build.id"), fingerprint=get("ro.build.fingerprint"),
        build_type=get("ro.build.type"), build_date=get("ro.build.date"), security_patch=get("ro.build.version.security_patch"),
        vendor_security_patch=get("ro.vendor.build.security_patch"), baseband=get("gsm.version.baseband"), bootloader=get("ro.bootloader"),
        kernel=data.get("UNAME") or None, kernel_full=data.get("KERNEL") or None, abi=get("ro.product.cpu.abi"),
        soc=get("ro.soc.model") or get("ro.board.platform"), resolution=resolution, density=integer(densities[-1]) if densities else None,
        slot=get("ro.boot.slot_suffix"), verified_boot=get("ro.boot.verifiedbootstate"), selinux=data.get("SELINUX") or None,
        uptime_s=int(uptime) if uptime is not None else None, mem_total=int(memory["MemTotal"]) * 1024 if "MemTotal" in memory else None,
        mem_available=int(memory["MemAvailable"]) * 1024 if "MemAvailable" in memory else None,
        battery=battery(data.get("BATTERY", ""), data.get("CURRENT", "")), storage=storage,
        partition_fingerprints={part: p[f"ro.{part}.build.fingerprint"] for part in ("system", "system_ext", "product", "vendor", "odm") if p.get(f"ro.{part}.build.fingerprint")})


def root_state(output):
    lines = output.splitlines()
    lines += [""] * max(0, 4 - len(lines))
    uid, build, mode, verified = lines[:4]
    mounts = re.findall(r" on (/system|/) type \S+ \(([^)]+)\)", "\n".join(lines[4:]))
    selected = [opts for mount, opts in mounts if mount == "/system"] or [opts for mount, opts in mounts if mount == "/"]
    writable = "rw" in selected[-1].split(",") if selected else None
    truth = {"enforcing": True, "eio": True, "disabled": False, "1": True, "0": False, "true": True, "false": False, "2": True}
    return M.RootState(uid.strip() == "0", writable, build.strip() or None, truth.get(mode.strip(), truth.get(verified.strip())))


def apex(output):
    result = []
    for line in output.splitlines():
        match = re.match(r"package:(.+?)(?:\s+versionCode:(\d+))?\s*$", line)
        if not match:
            continue
        body = match[1]
        path, name = body.rsplit("=", 1) if "=" in body else (None, body)
        result.append(M.ApexModule(name, integer(match[2]), path))
    return sorted(result, key=lambda item: item.name)
