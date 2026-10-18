# 设备样本

`.emu36` 为 emulator-5554 的真实 stdout；同名 `.stderr.emu36` 为真实 stderr。Android 16 / API 36、x86_64、userdebug、SELinux Enforcing；初始 adbd UID 2000，/system 为只读，verity=enforcing。设备时间使用 UTC，采样于 2026-10-18 UTC（本机 Asia/Shanghai 为 10 月 18–19 日）。没有其它厂商实机。

`commands.json` 记录 `collect_emu.py` 首批只读采样命令及退出码。补充采样如下：

- `adb_root` / `adb_unroot`、`root_state_root`、`proc_maps_root`、`lshal_root`、`dmesg_root`：短暂 adb root 后采样，finally 中 unroot，并核实 UID 恢复为 2000。没有 remount、disable-verity 或 reboot。
- `fastboot_version` / `fastboot_devices`、`scrcpy_version` / `scrcpy_help`：附带工具的主机输出；fastboot 设备列表为空，没有进入 fastboot。
- `dropbox_file`：`dumpsys dropbox --file`，保留原始文件名中的精确毫秒 ID；`dropbox_print` 为 `dumpsys dropbox --print SYSTEM_BOOT`。
- `stat_help` / `du_help` / `pkill_help`：对应 toybox 命令的帮助；`date_epoch` 为 `date +%s`。
- `ls_la_names`：在唯一命名的 `/data/local/tmp/dtb_sample_<uuid>/` 中建立中文、空格、单引号文件名及目录链接，`ls -la` 后 finally 删除整个专用目录。
- `init_start_nonroot`：非 root 执行 `start dtb_no_such_service_probe`，不存在该服务，退出码 1；未修改服务状态。
- `install_success` / `dumpsys_package_com.droidtoolbox.backendtest`：安装本仓库自建的独立无代码测试 APK 后采样，finally 卸载。未重装或清除设备原有应用。
- `bugreport_progress`：通过真实 AdbBackend 完整生成 Bug 报告时逐字节记录 stdout/stderr。约 21 秒生成 4,472,707 字节 ZIP，输出只有最终复制路径和拉取摘要，没有百分比；因此此设备的采集阶段为 `Progress(None, ...)`。执行前临时 root，完成后恢复 UID 2000；没有留下运行中的 dumpstate。ZIP 是本地验收产物，不进入 Git。
- `dumpsys_package_com.example.helloworld`：读取设备原有的非系统应用，仅作为带 `==` 路径和多组件的样本，没有修改其数据。

`.synthetic` 明确表示手写数据：多用户、需要重启的 remount、失败 remount、Bugreport 进度等不能安全在此模拟器上取得的格式。测试中的少量内联字符串也是明确构造的边界输入。

`apk/backend-test.apk` 由 `tests/build_test_apk.py` 使用 Python 标准库生成二进制 Manifest，再用本机 Android Studio JBR 的 keytool/jarsigner 签名。包名 `com.droidtoolbox.backendtest`，targetSdk 28，JAR v1 测试签名，无应用代码、无 Activity；只声明不触发的 Receiver 及 CAMERA 权限，用于包、组件和授权测试。它不访问相机。测试会拒绝覆盖预先存在的同名包，结束后卸载。密钥未保存，不用于生产。重新生成需要 JDK/JBR，不是后端或测试运行的依赖。

格式依据：[AOSP ResourceTypes.h](https://android.googlesource.com/platform/frameworks/base/+/67ad9a4/include/androidfw/ResourceTypes.h)，[Android 11 对 APK 签名的要求](https://developer.android.google.cn/about/versions/11/behavior-changes-11?hl=en)。
