"""演示后端用的样例数据（固定文本），行为逻辑在 mock.py。"""
from __future__ import annotations

import time

from .models import CrashFile, Device, PackageInfo

GB = 1024 ** 3
MB = 1024 ** 2

DEVICES = [
    Device("3A281FDH2000XY", "device", "usb", "Pixel 8 Pro", "Google", "husky", "15", 35, 82),
    Device("192.168.1.23:5555", "device", "wifi", "23127PN0CC", "Xiaomi", "houji", "14", 34, 64),
    Device("R5CW20ABCDE", "unauthorized", "usb"),
    Device("28011FDH30016K", "fastboot", "usb", product="panther"),
    Device("emulator-5554", "device", "emulator", "sdk_gphone64_x86_64", "Google", "emu64xa", "15", 35, 100),
]

LOG_TEMPLATES = [
    ("I", "ActivityManager", "Start proc {pid}:com.example.shop/u0a312 for top-activity {{com.example.shop/.MainActivity}}"),
    ("D", "OkHttp", "--> GET https://api.example.com/v2/catalog?page={n}"),
    ("W", "OkHttp", "<-- HTTP FAILED: java.net.SocketTimeoutException: timeout"),
    ("E", "AndroidRuntime", "FATAL EXCEPTION: main"),
    ("E", "AndroidRuntime", "java.lang.IllegalStateException: Catalog not loaded"),
    ("V", "GraphicsEnvironment", "ANGLE Developer option for 'com.example.shop' set to: 'default'"),
    ("D", "BatteryService", "Processing new values: level 82, voltage 4122, temperature 314"),
    ("I", "Choreographer", "Skipped {n} frames!  The application may be doing too much work on its main thread."),
    ("F", "libc", "Fatal signal 6 (SIGABRT), code -1 (SI_QUEUE) in tid 1077 (HwBinder:1028_2)"),
    ("I", "WindowManager", "Changing focus from Window{{a1b2 u0 Launcher}} to Window{{c3d4 u0 com.example.shop}}"),
    ("D", "SurfaceFlinger", "Display 4619827259835644672 HWC layers: 6"),
    ("W", "PackageManager", "Not granting permission android.permission.READ_PHONE_STATE to package com.example.shop"),
    ("I", "vold", "Trimmed 1824 MiB in 2381ms"),
    ("E", "storaged", "getDiskStats failed with result NOT_SUPPORTED and size 0"),
    ("D", "audio_hw_primary", "out_set_parameters: enter: usecase(1: low-latency-playback) kvpairs: routing=2"),
]

PROPS = {
    "ro.product.model": "Pixel 8 Pro", "ro.product.manufacturer": "Google", "ro.product.brand": "google",
    "ro.product.device": "husky", "ro.product.name": "husky", "ro.product.cpu.abi": "arm64-v8a",
    "ro.product.cpu.abilist": "arm64-v8a", "ro.build.version.release": "15", "ro.build.version.sdk": "35",
    "ro.build.version.security_patch": "2026-09-05", "ro.build.id": "AP4A.260905.002",
    "ro.build.type": "userdebug", "ro.build.tags": "test-keys",
    "ro.build.fingerprint": "google/husky/husky:15/AP4A.260905.002/12345678:userdebug/test-keys",
    "ro.boot.slot_suffix": "_a", "ro.boot.verifiedbootstate": "orange", "ro.boot.flash.locked": "0",
    "ro.boot.hardware": "husky", "ro.hardware": "husky", "ro.soc.model": "Tensor G3", "ro.board.platform": "zuma",
    "ro.debuggable": "1", "ro.secure": "1", "ro.adb.secure": "1", "ro.sf.lcd_density": "489",
    "ro.treble.enabled": "true", "ro.vndk.version": "35", "ro.zygote": "zygote64",
    "persist.sys.timezone": "Asia/Shanghai", "persist.sys.locale": "zh-CN", "persist.sys.usb.config": "adb",
    "sys.boot_completed": "1", "sys.usb.state": "adb", "init.svc.adbd": "running",
    "init.svc.surfaceflinger": "running", "init.svc.vold": "running", "dalvik.vm.heapsize": "512m",
    "dalvik.vm.heapgrowthlimit": "256m", "debug.hwui.renderer": "skiagl", "log.tag": "I",
    "net.bt.name": "Android", "gsm.operator.alpha": "中国移动", "gsm.network.type": "NR_SA",
    "vendor.display.lcd_density": "489", "wifi.interface": "wlan0",
    "ro.build.date": "Fri Sep  5 10:21:33 CST 2026", "ro.vendor.build.security_patch": "2026-08-05",
    "gsm.version.baseband": "g5300g-260801-260903-B-12345678", "ro.bootloader": "ripcurrent-15.2-12345678",
    "ro.system.build.fingerprint": "google/husky/husky:15/AP4A.260905.002/12345678:userdebug/test-keys",
    "ro.system_ext.build.fingerprint": "google/husky/husky:15/AP4A.260905.002/12345678:userdebug/test-keys",
    "ro.product.build.fingerprint": "google/husky/husky:15/AP4A.260905.002/12345678:userdebug/test-keys",
    "ro.vendor.build.fingerprint": "google/husky/husky:15/AP4A.260805.001/12100012:userdebug/test-keys",
    "ro.odm.build.fingerprint": "google/husky/husky:15/AP4A.260805.001/12100012:userdebug/test-keys",
    "init.svc.servicemanager": "running", "init.svc.hwservicemanager": "running", "init.svc.zygote": "running",
    "init.svc.zygote_secondary": "running", "init.svc.audioserver": "running", "init.svc.cameraserver": "running",
    "init.svc.media": "running", "init.svc.netd": "running", "init.svc.logd": "running", "init.svc.lmkd": "running",
    "init.svc.thermal-hal": "running", "init.svc.vendor.audio-hal": "running", "init.svc.vendor.camera-provider": "running",
    "init.svc.vendor.power-hal": "running", "init.svc.bootanim": "stopped", "init.svc.perfetto": "stopped",
    "init.svc.traced": "running", "init.svc.traced_probes": "running", "init.svc.vendor.wifi_hal": "restarting",
    "init.svc.statsd": "running", "init.svc.storaged": "running", "init.svc.tombstoned": "running",
}

# service list 的接口名（方括号里的内容）；没有列出的服务接口为空
BINDER_INTERFACES = {
    "activity": "android.app.IActivityManager", "alarm": "android.app.IAlarmManager",
    "appops": "com.android.internal.app.IAppOpsService", "audio": "android.media.IAudioService",
    "battery": "", "batterystats": "com.android.internal.app.IBatteryStats",
    "bluetooth_manager": "android.bluetooth.IBluetoothManager", "connectivity": "android.net.IConnectivityManager",
    "cpuinfo": "", "display": "android.hardware.display.IDisplayManager", "dropbox": "com.android.internal.os.IDropBoxManagerService",
    "gfxinfo": "", "input": "android.hardware.input.IInputManager", "input_method": "com.android.internal.view.IInputMethodManager",
    "jobscheduler": "android.app.job.IJobScheduler", "location": "android.location.ILocationManager",
    "media.audio_flinger": "android.media.IAudioFlingerService", "media.camera": "android.hardware.ICameraService",
    "meminfo": "", "netstats": "android.net.INetworkStatsService", "notification": "android.app.INotificationManager",
    "package": "android.content.pm.IPackageManager", "power": "android.os.IPowerManager", "procstats": "com.android.internal.app.procstats.IProcessStats",
    "sensorservice": "android.gui.SensorServer", "SurfaceFlinger": "android.ui.ISurfaceComposer",
    "telephony.registry": "com.android.internal.telephony.ITelephonyRegistry", "thermalservice": "android.os.IThermalService",
    "usagestats": "android.app.usage.IUsageStatsManager", "usb": "android.hardware.usb.IUsbManager",
    "vibrator_manager": "android.os.IVibratorManagerService", "wifi": "android.net.wifi.IWifiManager",
    "window": "android.view.IWindowManager",
    "android.hardware.power.IPower/default": "android.hardware.power.IPower",
    "android.hardware.vibrator.IVibrator/default": "android.hardware.vibrator.IVibrator",
    "android.hardware.light.ILights/default": "android.hardware.light.ILights",
    "android.hardware.thermal.IThermal/default": "android.hardware.thermal.IThermal",
    "android.hardware.health.IHealth/default": "android.hardware.health.IHealth",
    "android.hardware.camera.provider.ICameraProvider/internal/0": "android.hardware.camera.provider.ICameraProvider",
    "android.hardware.audio.core.IModule/default": "android.hardware.audio.core.IModule",
}
SERVICES = sorted(BINDER_INTERFACES, key=str.lower)

# (名称, 传输方式, 服务端 PID, 进程名)
HIDL_HALS = [
    ("android.hardware.audio@7.1::IDevicesFactory/default", "hwbinder", 1028, "android.hardware.audio.service"),
    ("android.hardware.bluetooth@1.1::IBluetoothHci/default", "hwbinder", 1102, "android.hardware.bluetooth@1.1-service"),
    ("android.hardware.gnss@2.1::IGnss/default", "hwbinder", 1140, "android.hardware.gnss@2.1-service"),
    ("android.hardware.graphics.composer@2.4::IComposer/default", "hwbinder", 880, "android.hardware.composer.hwc3"),
    ("android.hardware.wifi@1.6::IWifi/default", "hwbinder", 1201, "android.hardware.wifi@1.0-service"),
    ("android.hidl.manager@1.2::IServiceManager/default", "hwbinder", 520, "hwservicemanager"),
    ("android.hardware.graphics.mapper@4.0::IMapper/default", "passthrough", None, None),
]
AIDL_HAL_PROCESS = {
    "android.hardware.power.IPower/default": (1180, "android.hardware.power-service.pixel"),
    "android.hardware.vibrator.IVibrator/default": (1188, "android.hardware.vibrator-service.cs40l26"),
    "android.hardware.light.ILights/default": (1190, "android.hardware.lights-service.pixel"),
    "android.hardware.thermal.IThermal/default": (1170, "android.hardware.thermal-service.pixel"),
    "android.hardware.health.IHealth/default": (1050, "android.hardware.health-service.zuma"),
    "android.hardware.camera.provider.ICameraProvider/internal/0": (1230, "android.hardware.camera.provider-service.zuma"),
    "android.hardware.audio.core.IModule/default": (1028, "android.hardware.audio.service"),
}

# (pid, ppid, user, name, rss MB, state)
PROCESSES = [
    (1, 0, "root", "init", 9, "S"), (2, 0, "root", "kthreadd", 0, "S"), (520, 1, "system", "hwservicemanager", 6, "S"),
    (521, 1, "system", "servicemanager", 5, "S"), (602, 1, "logd", "logd", 14, "S"), (640, 1, "root", "vold", 11, "S"),
    (880, 1, "system", "android.hardware.composer.hwc3", 38, "S"), (921, 1, "system", "surfaceflinger", 118, "S"),
    (1028, 1, "audioserver", "android.hardware.audio.service", 42, "S"), (1050, 1, "system", "android.hardware.health-service.zuma", 8, "S"),
    (1170, 1, "system", "android.hardware.thermal-service.pixel", 9, "S"), (1180, 1, "system", "android.hardware.power-service.pixel", 7, "S"),
    (1230, 1, "cameraserver", "android.hardware.camera.provider-service.zuma", 64, "S"), (1201, 1, "wifi", "android.hardware.wifi@1.0-service", 10, "S"),
    (1300, 1, "root", "zygote64", 140, "S"), (1301, 1, "root", "zygote", 96, "S"), (1532, 1300, "system", "system_server", 412, "S"),
    (1610, 1, "audioserver", "audioserver", 36, "S"), (1640, 1, "cameraserver", "cameraserver", 30, "S"), (1700, 1, "shell", "adbd", 12, "S"),
    (2004, 1300, "u0_a108", "com.android.systemui", 221, "S"), (2911, 1300, "u0_a145", "com.google.android.gms.persistent", 166, "S"),
    (3120, 1300, "u0_a120", "com.google.android.apps.nexuslauncher", 92, "S"), (23011, 1300, "u0_a312", "com.example.shop", 288, "S"),
    (23540, 1300, "u10_a312", "com.example.shop", 120, "S"), (24102, 1300, "u0_a244", "com.tencent.mm", 343, "S"),
]

APEX = [
    ("com.android.adbd", 351010000), ("com.android.art", 351011020), ("com.android.btservices", 351010000),
    ("com.android.conscrypt", 351010000), ("com.android.media", 351011000), ("com.android.media.swcodec", 351011000),
    ("com.android.mediaprovider", 351011040), ("com.android.neuralnetworks", 351010000), ("com.android.os.statsd", 351010000),
    ("com.android.permission", 351011000), ("com.android.runtime", 1), ("com.android.sdkext", 351010000),
    ("com.android.tethering", 351010020), ("com.android.tzdata", 351010000), ("com.android.uwb", 351010000),
    ("com.android.vndk.v34", 1), ("com.android.wifi", 351011000), ("com.google.pixel.camera.hal", 35),
]

# (id, 名称, 类型, 运行中)
USERS = [(0, "机主", "主用户", True), (10, "工作资料", "工作资料", True), (999, "应用分身", "分身", True)]
# 非机主用户下安装的包
USER_PACKAGES = {
    10: ["android", "com.android.settings", "com.android.systemui", "com.android.phone", "com.android.shell",
         "com.google.android.gms", "com.android.chrome", "com.example.shop", "com.android.bluetooth"],
    999: ["android", "com.android.settings", "com.android.systemui", "com.android.shell", "com.google.android.gms",
          "com.tencent.mm"],
}

SETTINGS = {
    "system": {
        "accelerometer_rotation": "1", "font_scale": "1.0", "haptic_feedback_enabled": "1", "screen_brightness": "102",
        "screen_brightness_mode": "1", "screen_off_timeout": "60000", "show_touches": "0", "pointer_location": "0",
        "sound_effects_enabled": "0", "time_12_24": "24", "peak_refresh_rate": "120.0", "min_refresh_rate": "60.0",
        "volume_ring": "5", "volume_music": "8", "dtmf_tone": "1", "notification_light_pulse": "0",
    },
    "secure": {
        "android_id": "a1b2c3d4e5f60718", "accessibility_enabled": "0", "default_input_method":
            "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME",
        "enabled_accessibility_services": "null", "location_mode": "3", "screensaver_enabled": "0",
        "user_setup_complete": "1", "doze_always_on": "0", "navigation_mode": "2", "ui_night_mode": "1",
        "lock_screen_show_notifications": "1", "bluetooth_name": "Pixel 8 Pro", "immersive_mode_confirmations": "confirmed",
    },
    "global": {
        "adb_enabled": "1", "adb_wifi_enabled": "0", "airplane_mode_on": "0", "animator_duration_scale": "1.0",
        "transition_animation_scale": "1.0", "window_animation_scale": "1.0", "auto_time": "1", "auto_time_zone": "1",
        "bluetooth_on": "1", "development_settings_enabled": "1", "device_name": "Pixel 8 Pro", "device_provisioned": "1",
        "mobile_data": "1", "stay_on_while_plugged_in": "0", "wifi_on": "1", "wifi_scan_always_enabled": "1",
        "package_verifier_user_consent": "1", "verifier_verify_adb_installs": "0", "low_power": "0", "zen_mode": "0",
        "boot_count": "212", "hidden_api_policy": "null",
    },
}

# 每个包的组件（演示用，按包名生成）
COMPONENT_TEMPLATES = [
    ("activity", ".MainActivity", True), ("activity", ".settings.SettingsActivity", False), ("activity", ".debug.DebugActivity", False),
    ("service", ".sync.SyncService", False), ("service", ".push.PushService", True),
    ("receiver", ".BootReceiver", True), ("receiver", ".PackageReplacedReceiver", False),
    ("provider", ".data.ContentProvider", False),
]

PROC_STATUS = """Name:	{name}
Umask:	0077
State:	S (sleeping)
Tgid:	{pid}
Ngid:	0
Pid:	{pid}
PPid:	{ppid}
TracerPid:	0
Uid:	1000	1000	1000	1000
Gid:	1000	1000	1000	1000
FDSize:	512
Groups:	1001 1002 1003 1004 1005 1006 1007 1008 1009 1010 1018 1021 1023 1024 1032 1065 3001 3002 3003 3006 3007 3009 3011
VmPeak:	22842952 kB
VmSize:	21966180 kB
VmRSS:	{rss} kB
RssAnon:	{anon} kB
RssFile:	{file} kB
VmSwap:	18800 kB
Threads:	{threads}
SigBlk:	0000000000001204
SigCgt:	4000000e000084f8
CapEff:	0000000000000000
Seccomp:	2
voluntary_ctxt_switches:	88123
nonvoluntary_ctxt_switches:	12210
"""

BACKTRACE = """----- pid {pid} at 2026-10-18 19:22:41.118 -----
Cmd line: {name}
ABI: 'arm64'

"{name}" sysTid={pid}
    #00 pc 00000000000a1b24  /apex/com.android.runtime/lib64/bionic/libc.so (__epoll_pwait+4)
    #01 pc 0000000000019a40  /system/lib64/libutils.so (android::Looper::pollInner(int)+188)
    #02 pc 000000000001991c  /system/lib64/libutils.so (android::Looper::pollOnce(int, int*, int*, void**)+124)
    #03 pc 000000000000f2a8  /system/lib64/libbinder.so (android::IPCThreadState::joinThreadPool(bool)+56)

"binder:{pid}_1" sysTid={tid1}
    #00 pc 00000000000a07c4  /apex/com.android.runtime/lib64/bionic/libc.so (__ioctl+4)
    #01 pc 0000000000055a18  /system/lib64/libbinder.so (android::IPCThreadState::talkWithDriver(bool)+284)
    #02 pc 0000000000055c80  /system/lib64/libbinder.so (android::IPCThreadState::getAndExecuteCommand()+28)

----- end {pid} -----
"""

DMESG = [
    "init: Service 'vendor.wifi_hal' (pid 1201) exited with status 1",
    "init: Sending signal 9 to service 'vendor.wifi_hal' (pid 1201) process group...",
    "audit: type=1400 audit(1792300611.118:212): avc:  denied  {{ read }} for  pid={pid} comm=\"vendor.foo\" name=\"cfg\" dev=\"dm-5\" ino=1201 scontext=u:r:vendor_foo:s0 tcontext=u:object_r:vendor_data_file:s0 tclass=file permissive=0",
    "binder: 1532:1580 transaction failed 29189/-22, size 120-0 line 3064",
    "healthd: battery l=82 v=4122 t=31.4 h=2 st=2 c=-380000 fc=4850000 cc=212 chg=u",
    "lowmemorykiller: Kill 'com.example.bg' ({pid}), uid 10233, oom_score_adj 905 to free 88412kB",
    "thermal: cpu-big temperature {n}.2C, throttling level 0",
    "usb: android_work: sent uevent USB_STATE=CONFIGURED",
]

PACKAGES = [
    PackageInfo("com.example.shop", "3.12.0", 31200, False, True, "/data/app/~~x1/com.example.shop-1/base.apk", 10312, 35),
    PackageInfo("com.example.shop.debug", "3.13.0-dev", 31301, False, True, "/data/app/~~x2/com.example.shop.debug-1/base.apk", 10318, 35),
    PackageInfo("com.tencent.mm", "8.0.54", 2860, False, True, "/data/app/~~x3/com.tencent.mm-1/base.apk", 10244, 34),
    PackageInfo("com.eg.android.AlipayGphone", "10.7.10", 1077, False, True, "/data/app/~~x4/com.eg.android.AlipayGphone-1/base.apk", 10251, 33),
    PackageInfo("io.github.muntashirakon.AppManager", "4.0.1", 434, False, False, "/data/app/~~x5/io.github.muntashirakon.AppManager-1/base.apk", 10290, 35),
    PackageInfo("com.android.settings", "15", 35, True, True, "/system_ext/priv-app/Settings/Settings.apk", 1000, 35),
    PackageInfo("com.android.systemui", "15", 35, True, True, "/system_ext/priv-app/SystemUI/SystemUI.apk", 10108, 35),
    PackageInfo("com.android.phone", "15", 35, True, True, "/system/priv-app/TeleService/TeleService.apk", 1001, 35),
    PackageInfo("com.android.shell", "15", 35, True, True, "/system/priv-app/Shell/Shell.apk", 2000, 35),
    PackageInfo("com.google.android.gms", "26.38.33", 263833035, True, True, "/product/priv-app/GmsCore/GmsCore.apk", 10145, 35),
    PackageInfo("com.google.android.youtube", "20.39.37", 1554, True, False, "/product/app/YouTube/YouTube.apk", 10172, 35),
    PackageInfo("com.android.chrome", "141.0.7390.70", 739007033, True, True, "/product/app/Chrome/Chrome.apk", 10160, 35),
    PackageInfo("android", "15", 35, True, True, "/system/framework/framework-res.apk", 1000, 35),
    PackageInfo("com.android.bluetooth", "15", 35, True, True, "/apex/com.android.btservices/app/Bluetooth/Bluetooth.apk", 1002, 35),
]

FS = {
    "/": ["acct/", "apex/", "bin@", "config/", "data/", "dev/", "etc@", "mnt/", "odm/", "proc/", "product/",
          "sdcard@", "storage/", "sys/", "system/", "system_ext/", "vendor/"],
    "/sdcard": ["Alarms/", "Android/", "DCIM/", "Documents/", "Download/", "Movies/", "Music/", "Pictures/",
                "screenrecord_20261018.mp4", "bugreport-husky-2026-10-17.zip"],
    "/sdcard/Download": ["app-release.apk", "husky-ota-ap4a.zip", "notes.txt", "trace.perfetto-trace"],
    "/sdcard/DCIM": ["Camera/", "Screenshots/"],
    "/data": None,  # 无权限
    "/data/local/tmp": ["frida-server", "perfetto.cfg", "test.sh", "libtest.so"],
    "/system": ["apex/", "app/", "bin/", "build.prop", "etc/", "fonts/", "framework/", "lib/", "lib64/",
                "priv-app/", "usr/", "xbin/"],
    "/vendor": ["bin/", "build.prop", "etc/", "firmware/", "lib/", "lib64/", "overlay/"],
}

CRASHES = [
    CrashFile("tombstone", "/data/tombstones/tombstone_07", "tombstone_07", time.time() - 600, 284_112,
              "android.hardware.audio.service", "signal 6 (SIGABRT), code -1 (SI_QUEUE)"),
    CrashFile("tombstone", "/data/tombstones/tombstone_06", "tombstone_06", time.time() - 7200, 198_400,
              "com.example.shop", "signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0"),
    CrashFile("anr", "/data/anr/anr_2026-10-18-13-02-11-482", "anr_2026-10-18-13-02-11-482", time.time() - 1500,
              1_204_811, "com.example.shop", "Input dispatching timed out (5001ms)"),
    CrashFile("anr", "/data/anr/anr_2026-10-17-22-40-03-119", "anr_2026-10-17-22-40-03-119", time.time() - 54000,
              988_102, "com.android.systemui", "Broadcast of Intent { act=android.intent.action.SCREEN_OFF }"),
    CrashFile("dropbox", "dropbox:data_app_crash@1792300511000", "data_app_crash@1792300511000", time.time() - 900, 4_120,
              "com.example.shop", "java.lang.IllegalStateException: Catalog not loaded"),
    CrashFile("dropbox", "dropbox:system_server_wtf@1792290000000", "system_server_wtf@1792290000000", time.time() - 9000,
              2_311, "system_server", "WTF: Removing a non-existent window"),
    CrashFile("dropbox", "dropbox:SYSTEM_TOMBSTONE@1792299911000", "SYSTEM_TOMBSTONE@1792299911000", time.time() - 600,
              38_002, "android.hardware.audio.service", "signal 6 (SIGABRT)"),
]

TOMBSTONE_TEXT = """*** *** *** *** *** *** *** *** *** *** *** *** *** *** *** ***
Build fingerprint: 'google/husky/husky:15/AP4A.260905.002/12345678:userdebug/test-keys'
Revision: 'MP1.0'
ABI: 'arm64'
Timestamp: 2026-10-18 13:16:03.913+0800
Process uptime: 1823s
Cmdline: /vendor/bin/hw/android.hardware.audio.service
pid: 1028, tid: 1077, name: HwBinder:1028_2  >>> /vendor/bin/hw/android.hardware.audio.service <<<
uid: 1041
signal 6 (SIGABRT), code -1 (SI_QUEUE), fault addr --------
Abort message: 'FORTIFY: pthread_mutex_lock called on a destroyed mutex (0x7b2c4e10a0)'
    x0  0000000000000000  x1  0000000000000435  x2  0000000000000006  x3  0000007fd2a1c9f0
    x4  0000000000000000  x5  0000000000000000  x6  0000000000000000  x7  0000000000000008
    lr  0000007b3f1e2a44  sp  0000007fd2a1c9d0  pc  0000007b3f1e2a70  pst 0000000000001000

backtrace:
      #00 pc 000000000005ca70  /apex/com.android.runtime/lib64/bionic/libc.so (abort+164)
      #01 pc 00000000000611f4  /apex/com.android.runtime/lib64/bionic/libc.so (__fortify_fatal(char const*, ...)+124)
      #02 pc 00000000000c8d10  /apex/com.android.runtime/lib64/bionic/libc.so (HandleUsingDestroyedMutex(pthread_mutex_t*, char const*)+60)
      #03 pc 00000000000c8b94  /apex/com.android.runtime/lib64/bionic/libc.so (pthread_mutex_lock+208)
      #04 pc 0000000000034a20  /vendor/lib64/hw/audio.primary.zuma.so (StreamOutPrimary::write(void const*, unsigned long)+96)
      #05 pc 000000000001f4c8  /vendor/lib64/libaudiohal.so (android::hardware::audio::V7_1::StreamOut::writeThread()+412)
      #06 pc 00000000000c9f6c  /apex/com.android.runtime/lib64/bionic/libc.so (__pthread_start(void*)+204)
      #07 pc 000000000005e3d0  /apex/com.android.runtime/lib64/bionic/libc.so (__start_thread+64)
"""

ANR_TEXT = """----- pid 23011 at 2026-10-18 13:02:11.482 -----
Cmd line: com.example.shop
Build fingerprint: 'google/husky/husky:15/AP4A.260905.002/12345678:userdebug/test-keys'
ABI: 'arm64'
Subject: Input dispatching timed out (Waiting to send non-key event because the touched window has not finished processing certain input events that were delivered to it over 5000.0ms ago.)

"main" prio=5 tid=1 Blocked
  | group="main" sCount=1 ucsCount=0 flags=1 obj=0x72a1b2c8 self=0xb400007b3c0a2be0
  | sysTid=23011 nice=-10 cgrp=top-app sched=0/0 handle=0x7d1e2f44f8
  | state=S schedstat=( 1842211029 211002931 2410 ) utm=151 stm=33 core=4 HZ=100
  at com.example.shop.cache.DiskCache.get(DiskCache.kt:57)
  - waiting to lock <0x0a1b2c3d> (a java.lang.Object) held by thread 21
  at com.example.shop.catalog.CatalogRepository.load(CatalogRepository.kt:112)
  at com.example.shop.catalog.CatalogViewModel.refresh(CatalogViewModel.kt:64)
  at android.os.Handler.handleCallback(Handler.java:991)
  at android.os.Looper.loop(Looper.java:317)
  at android.app.ActivityThread.main(ActivityThread.java:8934)

"DiskIO-3" prio=5 tid=21 Native
  | group="main" sCount=1 ucsCount=0 flags=1 obj=0x13c40a88 self=0xb400007b3c11a7a0
  native: #00 pc 00000000000a1b24  /apex/com.android.runtime/lib64/bionic/libc.so (fsync+4)
  at java.io.FileDescriptor.sync(Native method)
  at com.example.shop.cache.DiskCache.flush(DiskCache.kt:88)
  - locked <0x0a1b2c3d> (a java.lang.Object)
"""

DUMPSYS = {
    "battery": """Current Battery Service state:
  AC powered: false
  USB powered: true
  Wireless powered: false
  Dock powered: false
  Max charging current: 3000000
  Max charging voltage: 5000000
  Charge counter: 3912000
  status: 2
  health: 2
  present: true
  level: 82
  scale: 100
  voltage: 4122
  temperature: 314
  technology: Li-ion
""",
    "meminfo": """Applications Memory Usage (in Kilobytes):
Uptime: 1823011 Realtime: 1823011

Total PSS by process:
    412,882K: system_server (pid 1532)
    288,104K: com.example.shop (pid 23011 / activities)
    221,770K: com.android.systemui (pid 2004)
    166,340K: com.google.android.gms.persistent (pid 2911)
    118,226K: surfaceflinger (pid 921)
     92,410K: com.android.launcher3 (pid 3120 / activities)

Total RAM: 11,716,328K (status normal)
 Free RAM: 4,912,220K (  622,340K cached pss + 3,601,880K cached kernel +   688,000K free)
 Used RAM: 6,240,108K (5,102,440K used pss + 1,137,668K kernel)
 Lost RAM:   564,000K
     ZRAM:   312,004K physical used for 1,204,388K in swap (4,194,300K total swap)
""",
    "activity": """ACTIVITY MANAGER ACTIVITIES (dumpsys activity activities)
Display #0 (activities from top to bottom):
  * Task{a1b2c3 #1204 type=standard A=10312:com.example.shop U=0 visible=true mode=fullscreen}
    * ActivityRecord{d4e5f6 u0 com.example.shop/.MainActivity t1204}
      state=RESUMED  visible=true
  * Task{b2c3d4 #1 type=home A=10120:com.google.android.apps.nexuslauncher U=0 visible=false}
""",
    "SurfaceFlinger": """Build configuration: [sf PRESENT_TIME_OFFSET=0 FORCE_HWC_FOR_RBG_TO_YUV=1 MAX_VIRT_DISPLAY_DIM=4096 RUNNING_WITHOUT_SYNC_FRAMEWORK=0 NUM_FRAMEBUFFER_SURFACE_BUFFERS=3]
Display 4619827259835644672 (physical, "Built-in Screen")
   connectionType=Internal
   colorModes=
    ColorMode::NATIVE
    ColorMode::SRGB
    ColorMode::DISPLAY_P3
   activeColorMode=ColorMode::SRGB
   refresh rate: 120.00 Hz
Visible layers (count = 6)
""",
}



def fake_png(w: int, h: int) -> bytes:
    """生成一张纯色带条纹的 PNG（只用标准库），模拟截屏。"""
    import struct
    import zlib
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            if y < 24:
                row += bytes((32, 33, 36))
            elif 60 < y < 200 and 16 < x < w - 16:
                row += bytes((61, 220, 132)) if (y // 20) % 2 else bytes((44, 160, 96))
            elif (y - 220) % 64 < 48 and y > 220 and 16 < x < w - 16:
                row += bytes((52, 54, 58))
            else:
                row += bytes((24, 25, 28))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xffffffff)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
