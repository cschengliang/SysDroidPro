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
