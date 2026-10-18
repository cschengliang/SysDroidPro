from dataclasses import replace

import pytest

from app.backend import parsers as P
from tests.fakes import fixture


def test_real_devices_props_detail():
    devs = P.devices(fixture("devices_l.emu36"))
    assert devs[0].serial == "emulator-5554" and devs[0].transport == "emulator"
    props = P.props(fixture("getprop.emu36"))
    assert props["ro.build.version.sdk"] == "36"
    assert all(v.startswith("u:object_r:") for v in P.props(fixture("getprop_Z.emu36")).values())
    detail = P.device_detail("s", fixture("detail.emu36"))
    assert detail.api == 36 and detail.abi == "x86_64" and detail.kernel_full.startswith("Linux version")
    assert detail.mem_total > detail.mem_available > 0
    assert detail.battery.level == 90 and detail.storage
    assert len(detail.partition_fingerprints) == 5


def test_device_variants():
    data = "* daemon started successfully\nusb device model:Pixel_8 device:husky\n1.2.3.4:5555 offline\nadb-x._adb-tls-connect._tcp unauthorized\n"
    devs = P.devices(data)
    assert [d.transport for d in devs] == ["usb", "wifi", "wifi"]
    assert devs[0].model == "Pixel 8"
    assert P.devices("s\tfastboot\n", fastboot=True)[0].state == "fastboot"
    assert P.devices("") == []


def test_multiline_empty_and_chinese_props():
    assert P.props("[a]: [第一行\n第二行]\n[b]: []\n[c]: [a=b]\n") == {"a": "第一行\n第二行", "b": "", "c": "a=b"}


def test_battery_missing_and_units():
    assert P.battery("") is None
    bat = P.battery("level: 50\nstatus: 2\ntemperature: 314\nvoltage: 4122\nWireless powered: true", "-1200000")
    assert (bat.current_ma, bat.plugged, bat.temperature_c, bat.voltage_v) == (1200, "无线", 31.4, 4.122)
    assert P.battery("status: 3", "1200000").current_ma == -1200
    assert replace(bat, level=None).level is None


@pytest.mark.parametrize("name,root", [("root_state_nonroot.emu36", False), ("root_state_root.emu36", True)])
def test_root_state_real(name, root):
    state = P.root_state(fixture(name))
    assert state.adbd_root is root and state.system_writable is False and state.verity_enabled is True


def test_root_unknown_override_and_numbers():
    assert P.root_state("2000\nuser\n\n\n").verity_enabled is None
    assert P.root_state("0\nuserdebug\ndisabled\n\noverlay on /system type overlay (rw,relatime)").system_writable is True
    dd = P.device_detail("s", "==WM\nPhysical size: 1080x2400\nOverride size: 540x1200\nPhysical density: 420\nOverride density: 210")
    assert dd.resolution == "540 × 1200（覆盖）" and dd.density == 210
    assert dd.api is None and dd.battery is None
    assert P.integer("bad") is None and P.number("bad") is None
    assert P.sections("ignored\n==MEM\nfoo\n==WM\nbar") == {"MEM": "foo", "WM": "bar"}


def test_apex():
    modules = P.apex(fixture("apex_list.emu36"))
    assert modules and modules[0].version_code is not None
    assert modules == sorted(modules, key=lambda item: item.name)
    assert P.apex("package:/a==/x.apex=com.example.x versionCode:12\n")[0].path == "/a==/x.apex"


def test_settings_services_and_hals():
    assert P.settings("a=x=y\nnull=null\nempty=\n") == {"a": "x=y", "null": "null", "empty": ""}
    for ns in ("system", "secure", "global"):
        assert P.settings(fixture(f"settings_list_{ns}.emu36"))
    services = P.binder_services(fixture("service_list.emu36"))
    assert services and any(row.interface is None for row in services)
    assert [s.name for s in services] == sorted((s.name for s in services), key=str.lower)
    assert P.init_services({"init.svc.z": "running", "init.svc.a": "stopped", "other": "no"})[0].name == "a"
    for name in ("lshal.emu36", "lshal_root.emu36", "lshal_fallback.emu36"):
        rows = P.hidl_hals(fixture(name))
        if name != "lshal_fallback.emu36":
            assert rows and any(row.pid is not None for row in rows)
        assert all("*" not in row.name for row in rows)


def test_processes_and_partial_detail():
    rows = P.processes(fixture("ps_A.emu36"))
    assert rows[0].pid == 1 and rows[0].rss > 0
    assert P.processes("PID PPID USER RSS S NAME\n1 0 root 0 S init")[0].rss == 0
    output = "==STATUS\n" + fixture("proc_status.emu36") + "\n==CMDLINE\ninit\0x\n==THREADS\n1 init\n2 worker thread\n==LIBS\n" + fixture("proc_maps_root.emu36") + "\n==FD\n"
    detail = P.process_detail(1, output)
    assert detail.name == "init" and detail.cmdline == "init x" and detail.fd_count is None
    assert detail.threads == [(1, "init"), (2, "worker thread")]
    assert detail.libraries and len(detail.libraries) == len(set(detail.libraries))
    assert P.process_names("PID NAME\n10 android.hardware.foo\n") == {10: "android.hardware.foo"}


def test_real_and_fallback_users():
    for name in ("cmd_user_list_v.emu36", "pm_list_users.emu36"):
        rows = P.users(fixture(name))
        assert rows[0].id == 0 and rows[0].kind == "主用户" and rows[0].running
    rows = P.users("1: id=10, name=null, type=profile.MANAGED, flags=0\n2: id=999, name=分身, type=profile.CLONE, flags=0 (running)\n3: id=11, name=Guest, type=full.GUEST, flags=0")
    assert [(r.id, r.kind) for r in rows] == [(10, "工作资料"), (11, "访客"), (999, "分身")]
    assert rows[0].name == "用户 10"


def test_real_package_paths_metadata_permissions():
    rows = P.packages(fixture("pm_list_packages_user0.emu36"), fixture("pm_list_packages_system.emu36"),
                      fixture("pm_list_packages_disabled.emu36"), fixture("dumpsys_package_packages.emu36"))
    hello = next(row for row in rows if row.name == "com.example.helloworld")
    assert "==/" in hello.apk_path and hello.version_code == 1 and hello.version_name == "1.0" and hello.target_sdk == 36
    assert not hello.system and hello.enabled
    for name in ("android", "com.android.settings", "com.example.helloworld"):
        output = fixture(f"dumpsys_package_{name}.emu36")
        detail = P.package_detail(output, name)
        assert detail.info.name == name and detail.installed_users == [0]
        assert detail.data_dir.startswith("/data/") and detail.min_sdk is not None
    assert P.package_detail(fixture("dumpsys_package_missing.emu36"), "missing") is None


def test_package_users_and_components():
    output = fixture("package_multiuser.synthetic")
    zero, ten = P.package_detail(output, "com.example.test"), P.package_detail(output, "com.example.test", 10)
    assert zero.installed_users == [0, 10] and ten.data_dir == "/data/user/10/com.example.test"
    assert ten.info.uid == 1012345
    assert next(p for p in zero.permissions if p.name.endswith("CAMERA")).granted
    assert not next(p for p in ten.permissions if p.name.endswith("CAMERA")).granted
    assert not next(p for p in ten.permissions if p.name.endswith("RECORD_AUDIO")).runtime
    assert P.package_detail(output, "com.example.test", 11) is None
    comps = P.components(output, "com.example.test")
    assert not next(c for c in comps if c.kind == "activity").enabled
    assert next(c for c in P.components(output, "com.example.test", 10) if c.kind == "activity").enabled
    service = next(c for c in comps if c.kind == "service")
    assert service.exported is False and service.permission == "android.permission.BIND_JOB_SERVICE"
    provider = next(c for c in comps if c.kind == "provider")
    assert provider.authority == "com.example.test.provider" and provider.exported is None
    assert P.component_class("com.p", "Foo") == "com.p.Foo"


def test_real_components_deduplicated():
    rows = P.components(fixture("dumpsys_package_com.android.settings.emu36"), "com.android.settings")
    assert len(rows) > 100 and len(rows) == len({(r.kind, r.name) for r in rows})
    assert any(row.kind == "provider" and row.authority for row in rows)
    assert any(not row.enabled for row in rows)


@pytest.mark.parametrize("code", ["INSTALL_FAILED_VERSION_DOWNGRADE", "INSTALL_FAILED_UPDATE_INCOMPATIBLE", "INSTALL_FAILED_ALREADY_EXISTS",
    "INSTALL_FAILED_INSUFFICIENT_STORAGE", "INSTALL_FAILED_OLDER_SDK", "INSTALL_FAILED_NO_MATCHING_ABIS", "INSTALL_FAILED_TEST_ONLY",
    "INSTALL_PARSE_FAILED_NO_CERTIFICATES", "INSTALL_FAILED_USER_RESTRICTED", "INSTALL_FAILED_INVALID_APK", "INSTALL_PARSE_FAILED_MANIFEST_MALFORMED", "INSTALL_FAILED_OTHER"])
def test_install_errors(code):
    assert any("\u4e00" <= char <= "\u9fff" for char in P.install_error(f"Failure [{code}: detail]"))


def test_progress_and_fields():
    progress = fixture("bugreport_progress.synthetic")
    assert P.progress_percent(progress) == 85 and P.progress_percent("no percent") is None
    assert P.bugreport_path(progress) == "C:/output/bugreport-test.zip"
    assert P.field("  a=null", "a") is None
    assert P.token("a=true b=1", "b") == "1"


def test_real_file_names_links_and_nodes():
    rows = P.file_entries(fixture("ls_la_names.emu36"), "/data/local/tmp/samples")
    assert {row.name for row in rows} == {"中文 a'b.txt", "system link"}
    assert next(row for row in rows if row.is_link).link_target == "/system"
    nodes = P.file_entries(fixture("ls_la_dev.emu36"), "/dev")
    assert next(row for row in nodes if row.name == "ashmem").size is None
    for name in ("ls_la_root.emu36", "ls_la_sdcard.emu36", "anr.emu36", "tombstones.emu36"):
        assert isinstance(P.file_entries(fixture(name), "/"), list)


def test_dropbox_exact_millis_and_timezone():
    offset = P.timezone_offset(fixture("date_offset.emu36"))
    rows = P.dropbox_files(fixture("dropbox_file.emu36"), offset)
    boot = next(row for row in rows if row.name.startswith("SYSTEM_BOOT@"))
    assert str(round(boot.mtime * 1000)) in boot.path
    assert all("strictmode" not in row.name for row in rows)
    selected = P.dropbox_content(fixture("dropbox_print.emu36"), "SYSTEM_BOOT", round(boot.mtime * 1000), offset, fixture("dropbox_file.emu36"))
    assert selected and "Build:" in selected
    assert P.dropbox_files(fixture("dropbox.emu36"), offset)
    assert P.timestamp("2026-10-18 12:00", P.timezone_offset("+0800")) == P.timestamp("2026-10-18 04:00:00", P.timezone_offset("+0000"))
    assert P.timestamp("bad", offset) is None and P.timestamp("2026-10-18 12:00", None) is None
    assert P.timezone_offset("+2460") is None and P.timezone_offset("bad") is None


def test_dropbox_same_second_and_summary():
    offset = P.timezone_offset("+0000")
    listing = "2026-01-01 00:00:00 data_app_crash (text, 1 bytes)\n /data/system/dropbox/data_app_crash@1767225600123.txt\n2026-01-01 00:00:00 data_app_crash (text, 1 bytes)\n /data/system/dropbox/data_app_crash@1767225600456.txt\n"
    printed = "2026-01-01 00:00:00 data_app_crash (text, 1 bytes)\nfirst\n2026-01-01 00:00:00 data_app_crash (text, 1 bytes)\nsecond\n"
    assert P.dropbox_content(printed, "data_app_crash", 1767225600456, offset, listing) == "second"
    assert P.dropbox_content(printed, "data_app_crash", 1767225600456, offset) is None
    assert P.crash_summary("Process: com.example\njava.lang.IllegalStateException: bad\n") == ("com.example", "java.lang.IllegalStateException: bad")
