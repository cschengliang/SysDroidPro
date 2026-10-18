"""Android 命令输出的纯解析器；缺失的可选字段保持 None。"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
import posixpath

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
        if re.fullmatch(r"==[A-Z_0-9]+", line):
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


def settings(output):
    return dict(line.split("=", 1) for line in output.splitlines() if "=" in line)


def binder_services(output):
    rows = []
    for line in output.splitlines():
        match = re.match(r"\s*\d+\s+(\S+):\s*\[(.*?)\]", line)
        if match:
            rows.append(M.BinderService(match[1], match[2] or None))
    return sorted(rows, key=lambda row: row.name.lower())


def init_services(properties):
    return sorted((M.InitService(k[9:], v) for k, v in properties.items() if k.startswith("init.svc.")), key=lambda row: row.name.lower())


def hidl_hals(output):
    rows = {}
    for line in output.splitlines():
        match = re.search(r"(\S+@\S+::\S+)\s+.*?\b(hwbinder|passthrough)\s+(\d+|N/A)\b", line)
        if match and "*" not in match[1]:
            row = M.HalInfo(match[1], "hidl", match[2], integer(match[3]))
            rows[row.name, row.transport] = row
    return sorted(rows.values(), key=lambda row: (row.name, row.transport))


def processes(output):
    rows = []
    for line in output.splitlines():
        cells = line.split(None, 5)
        if len(cells) == 6 and integer(cells[0]) is not None:
            rss = integer(cells[3])
            rows.append(M.ProcessInfo(int(cells[0]), integer(cells[1]), cells[2], cells[5],
                                      rss * 1024 if rss is not None else None, cells[4]))
    return rows


def process_names(output):
    result = {}
    for line in output.splitlines():
        cells = line.split(None, 1)
        if len(cells) == 2 and integer(cells[0]) is not None:
            result[int(cells[0])] = cells[1]
    return result


def process_detail(pid, output):
    data = sections(output)
    status = data.get("STATUS", "")
    name = re.search(r"(?m)^Name:\s*(.+)$", status)
    threads = []
    for line in data.get("THREADS", "").splitlines():
        cells = line.split(None, 1)
        if len(cells) == 2 and integer(cells[0]) is not None:
            threads.append((int(cells[0]), cells[1]))
    libraries = set()
    for line in data.get("LIBS", "").splitlines():
        match = re.search(r"(/\S+\.so(?:\.\d+)*)\b", line)
        if match:
            libraries.add(match[1])
    cmdline = data.get("CMDLINE", "").replace("\0", " ").strip()
    return M.ProcessDetail(pid, name[1] if name else (cmdline.split(" ")[0] if cmdline else str(pid)),
                           cmdline, status, sorted(threads), sorted(libraries), integer(data.get("FD", "").strip()))


def users(output):
    result = []
    for line in output.splitlines():
        match = re.search(r"\bid=(\d+), name=(.*?), type=([^,\s]+)", line)
        flags = 0
        if match:
            uid, name, kind = int(match[1]), match[2], match[3]
        else:
            match = re.search(r"UserInfo\{(\d+):(.*):([\da-fA-F]+)\}", line)
            if not match:
                continue
            uid, name, kind = int(match[1]), match[2], ""
            flags = int(match[3], 16)
        label = "主用户" if uid == 0 else "工作资料" if "MANAGED" in kind or flags & 0x20 else "分身" if "CLONE" in kind else "访客" if "GUEST" in kind or flags & 0x4 else "其它"
        if not name or name == "null":
            name = "机主" if uid == 0 else f"用户 {uid}"
        result.append(M.AndroidUser(uid, name, label, "running" in line.lower()))
    return sorted(result, key=lambda row: row.id)


def package_blocks(output):
    pattern = r"(?ms)^  Package \[([^\]]+)\][^\n]*\n(.*?)(?=^  Package \[|^\S|\Z)"
    return {match[1]: match[2] for match in re.finditer(pattern, output)}


def user_blocks(output):
    matches = list(re.finditer(r"(?m)^    User (\d+):[^\n]*", output))
    return {int(match[1]): output[match.start():matches[i + 1].start() if i + 1 < len(matches) else len(output)]
            for i, match in enumerate(matches)}


def field(output, key):
    match = re.search(r"(?m)^\s*" + re.escape(key) + r"=(.*)$", output)
    return match[1].strip() if match and match[1].strip() != "null" else None


def token(output, key):
    match = re.search(r"\b" + re.escape(key) + r"=(\S+)", output)
    return match[1] if match else None


def indented(output, title):
    lines = output.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == title + ":":
            indent = len(line) - len(line.lstrip())
            result = []
            for child in lines[index + 1:]:
                if child.strip() and len(child) - len(child.lstrip()) <= indent:
                    break
                result.append(child.strip())
            return result
    return []


def package_names(output):
    return {line[8:].strip() for line in output.splitlines() if line.startswith("package:")}


def packages(output, system="", disabled="", metadata=""):
    system_names, disabled_names = package_names(system), package_names(disabled)
    blocks = package_blocks(metadata)
    result = []
    for line in output.splitlines():
        if not line.startswith("package:"):
            continue
        body, *extra = line[8:].split()
        if "=" in body:
            path, name = body.rsplit("=", 1)
        else:
            path, name = None, body
        options = dict(item.split(":", 1) for item in extra if ":" in item)
        block = blocks.get(name, "")
        result.append(M.PackageInfo(name, field(block, "versionName"), integer(options.get("versionCode")),
            name in system_names, name not in disabled_names, path, integer(options.get("uid")), integer(token(block, "targetSdk"))))
    return sorted(result, key=lambda row: row.name)


def package_detail(output, package, user=0, info=None, launchable=False):
    block = package_blocks(output).get(package)
    if block is None:
        return None
    all_users = user_blocks(block)
    selected = all_users.get(user, "")
    installed = sorted(uid for uid, body in all_users.items() if token(body.splitlines()[0], "installed") == "true")
    if user not in installed:
        return None
    enabled = token(selected.splitlines()[0], "enabled") not in ("2", "3", "4")
    if info is None:
        app_id = integer(field(block, "appId") or field(block, "userId"))
        info = M.PackageInfo(package, field(block, "versionName"), integer(token(block, "versionCode")),
            bool(re.search(r"flags=\[\s*SYSTEM\b", block)), enabled, None,
            user * 100000 + app_id % 100000 if app_id is not None else None, integer(token(block, "targetSdk")))
    permissions = {name: M.Permission(name, False, False) for name in indented(block, "requested permissions") if name and ":" not in name}
    for runtime, lines in ((False, indented(block, "install permissions")), (True, indented(selected, "runtime permissions"))):
        for line in lines:
            match = re.match(r"([^:\s]+): granted=(true|false)", line)
            if match:
                permissions[match[1]] = M.Permission(match[1], match[2] == "true", runtime)
    directory = field(selected, "dataDir") or field(block, "dataDir")
    if directory and user != 0:
        directory = re.sub(r"^/data/(?:data/|user/0/)", f"/data/user/{user}/", directory)
        directory = re.sub(r"^/data/user_de/0/", f"/data/user_de/{user}/", directory)
    return M.PackageDetail(info, integer(token(block, "minSdk")), directory, field(selected, "firstInstallTime") or field(block, "firstInstallTime"),
        field(block, "lastUpdateTime"), field(block, "installerPackageName"), sorted(permissions.values(), key=lambda row: row.name), launchable, installed)


def component_class(package, cls):
    return package + cls if cls.startswith(".") else package + "." + cls if "." not in cls else cls


def components(output, package, user=0):
    block = package_blocks(output).get(package, "")
    selected = user_blocks(block).get(user, "")
    disabled = {component_class(package, name) for name in indented(selected, "disabledComponents")}
    enabled = {component_class(package, name) for name in indented(selected, "enabledComponents")}
    package_enabled = token(selected.splitlines()[0], "enabled") not in ("2", "3", "4") if selected else True
    kinds = {"Activity Resolver Table:": "activity", "Activities:": "activity", "Service Resolver Table:": "service", "Services:": "service",
             "Receiver Resolver Table:": "receiver", "Receivers:": "receiver", "Provider Resolver Table:": "provider", "Providers:": "provider",
             "Registered ContentProviders:": "provider", "ContentProvider Authorities:": "provider"}
    kind, current, authority = None, None, None
    rows = {}
    for line in output.splitlines():
        stripped = line.strip()
        if stripped in kinds or (line and not line[0].isspace()):
            kind, current, authority = kinds.get(stripped), None, None
        if kind is None:
            continue
        match_auth = re.match(r"\s*\[([^\]]+)\]:$", line)
        if match_auth:
            authority = match_auth[1]
        match = re.search(re.escape(package) + r"/([\w.$]+)", line)
        if match:
            name = component_class(package, match[1])
            current = (kind, name)
            rows.setdefault(current, {"exported": None, "enabled": True, "permission": None, "authorities": set()})
            if kind == "provider" and authority:
                rows[current]["authorities"].add(authority)
        if current:
            row = rows[current]
            for key in ("exported", "enabled"):
                value = token(line, key)
                if value in ("true", "false"):
                    row[key] = value == "true"
            permission = token(line, "permission")
            if permission and permission != "null":
                row["permission"] = permission
            provider_authority = token(line, "authority")
            if kind == "provider" and provider_authority and provider_authority != "null":
                row["authorities"].add(provider_authority)
    return [M.Component(kind, name, data["exported"], package_enabled and (name in enabled or (name not in disabled and data["enabled"])),
                        data["permission"], ";".join(sorted(data["authorities"])) or None)
            for (kind, name), data in sorted(rows.items())]


def progress_percent(output):
    matches = re.findall(r"\[\s*(\d+)%\]", output)
    return min(100, max(0, int(matches[-1]))) if matches else None


def bugreport_path(output):
    match = re.search(r"Bug report copied to\s+(.+)", output, re.I)
    return match[1].strip().strip('"') if match else None


def install_error(output):
    match = re.search(r"(?:INSTALL_FAILED|INSTALL_PARSE_FAILED)_[A-Z_]+", output)
    code = match[0] if match else "未知错误"
    translations = {
        "INSTALL_FAILED_VERSION_DOWNGRADE": "版本低于已安装版本，需勾选“允许降级”",
        "INSTALL_FAILED_UPDATE_INCOMPATIBLE": "签名与已安装版本不一致，需先卸载旧版本",
        "INSTALL_FAILED_ALREADY_EXISTS": "应用已存在，需勾选“覆盖安装”",
        "INSTALL_FAILED_INSUFFICIENT_STORAGE": "设备存储空间不足",
        "INSTALL_FAILED_OLDER_SDK": "设备系统版本低于应用要求的 minSdk",
        "INSTALL_FAILED_NO_MATCHING_ABIS": "应用不支持该设备的 CPU 架构",
        "INSTALL_FAILED_TEST_ONLY": "测试包，需勾选“允许测试包”",
        "INSTALL_PARSE_FAILED_NO_CERTIFICATES": "APK 未签名",
        "INSTALL_FAILED_USER_RESTRICTED": "用户拒绝了安装，请检查设备的 USB 安装开关",
        "INSTALL_FAILED_INVALID_APK": "APK 文件无效或已损坏",
    }
    return translations.get(code, "APK 文件无效或已损坏" if code.startswith("INSTALL_PARSE_FAILED_") else f"安装失败：{code}")


def file_entries(output, path):
    pattern = r"^([bcdlps-][rwxstST-]{9}[+.@]?)\s+\d+\s+(\S+)\s+(\S+)\s+(\d+,\s*\d+|\d+)\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}(?::\d{2})?)\s+(.*)$"
    result = []
    for line in output.splitlines():
        match = re.match(pattern, line)
        if not match:
            continue
        mode, owner, group, size, date, clock, name = match.groups()
        target = None
        if mode.startswith("l"):
            name, sep, target = name.partition(" -> ")
            if not sep:
                target = None
        if name in (".", ".."):
            continue
        result.append(M.FileEntry(name, posixpath.join(path, name), mode.startswith("d"), mode.startswith("l"), target,
                                  integer(size), mode, owner, group, date + " " + clock))
    return sorted(result, key=lambda row: (not row.is_dir, row.name.casefold()))


def timezone_offset(output):
    match = re.fullmatch(r"([+-])(\d{2})(\d{2})", output.strip())
    if not match:
        return None
    if int(match[3]) >= 60:
        return None
    minutes = (int(match[2]) * 60 + int(match[3])) * (1 if match[1] == "+" else -1)
    return timezone(timedelta(minutes=minutes)) if abs(minutes) < 24 * 60 else None


def timestamp(value, offset):
    if offset is None:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=offset).timestamp()
        except ValueError:
            continue
    return None


def crash_summary(output):
    process = re.search(r"(?m)^(?:Cmdline|Cmd line|Process):\s*(.+)", output)
    summary = re.search(r"(?m)^(signal .+|Subject:\s*.+|[\w.$]+(?:Exception|Error):.*)", output)
    return (process[1] if process else None, summary[1].removeprefix("Subject:").strip() if summary else None)


def dropbox_records(output, offset):
    pattern = r"(?m)^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?) (\S+) \(([^\n]*)\)\s*$"
    matches = list(re.finditer(pattern, output))
    result = []
    for i, match in enumerate(matches):
        body = output[match.end():matches[i + 1].start() if i + 1 < len(matches) else len(output)]
        raw_path = re.search(r"(?m)^\s*/data/system/dropbox/[^\n]*@(\d+)\.(?:txt|dat|lost)(?:\.gz)?\s*$", body)
        epoch = timestamp(match[1], offset)
        millis = int(raw_path[1]) if raw_path else round(epoch * 1000) if epoch is not None else None
        size_match = re.search(r"(\d+) bytes", match[3])
        result.append({"tag": match[2], "stamp": match[1], "millis": millis,
                       "size": int(size_match[1]) if size_match else None, "body": body.strip()})
    return result


def dropbox_files(output, offset):
    rows = []
    for entry in dropbox_records(output, offset):
        tag, millis = entry["tag"], entry["millis"]
        if millis is None or not (tag.lower().endswith(("_crash", "_anr", "_wtf")) or tag in ("SYSTEM_TOMBSTONE", "SYSTEM_LAST_KMSG", "SYSTEM_BOOT")):
            continue
        name = f"{tag}@{millis}"
        process, summary = crash_summary(entry["body"])
        rows.append(M.CrashFile("dropbox", "dropbox:" + name, name, millis / 1000, entry["size"], process, summary))
    return sorted(rows, key=lambda row: row.mtime, reverse=True)


def dropbox_content(output, tag, millis, offset, index_output=""):
    printed = [entry for entry in dropbox_records(output, offset) if entry["tag"] == tag]
    indexed = [entry for entry in dropbox_records(index_output, offset) if entry["tag"] == tag]
    target = next((entry for entry in indexed if entry["millis"] == millis), None)
    if target:
        group = [entry for entry in indexed if entry["stamp"] == target["stamp"]]
        candidates = [entry for entry in printed if entry["stamp"] == target["stamp"]]
        if len(group) != len(candidates):
            return None
        return candidates[group.index(target)]["body"].split("\nUsage: dumpsys dropbox")[0].strip().rstrip("=").strip()
    candidates = [entry for entry in printed if entry["millis"] is not None and entry["millis"] // 1000 == millis // 1000]
    if len(candidates) == 1:
        return candidates[0]["body"].split("\nUsage: dumpsys dropbox")[0].strip().rstrip("=").strip()
    return None
