"""生成没有应用代码或 Activity 的独立测试包；不需要 Android build-tools。

用法：python tests/build_test_apk.py --jdk <JDK/JBR 目录>
AXML 布局来自 AOSP ResourceTypes.h；target 28 可使用 JAR v1 签名。
签名密钥临时生成，只用于本测试 APK，不用于生产分发。
"""
import argparse
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile

PACKAGE = "com.droidtoolbox.backendtest"
NO_INDEX = 0xffffffff


def manifest():
    attrs = {"versionCode": 0x0101021b, "versionName": 0x0101021c, "minSdkVersion": 0x0101020c,
             "targetSdkVersion": 0x01010270, "hasCode": 0x0101000c, "label": 0x01010001,
             "name": 0x01010003, "exported": 0x01010010, "enabled": 0x0101000e}
    strings = [*attrs, "http://schemas.android.com/apk/res/android", "android", "manifest", "uses-sdk", "application", "package", PACKAGE, "1.0", "Droidtoolbox Backend Test",
               "uses-permission", "android.permission.CAMERA", "receiver", "android.content.BroadcastReceiver", "intent-filter", "action", PACKAGE + ".TEST"]
    indexes = {value: i for i, value in enumerate(strings)}
    data, offsets = b"", []
    for value in strings:
        raw = value.encode("utf-8")
        offsets.append(len(data))
        data += bytes((len(value), len(raw))) + raw + b"\0"
    data += b"\0" * (-len(data) % 4)
    pool = struct.pack("<HH6I", 1, 28, 28 + len(strings) * 4 + len(data), len(strings), 0, 0x100, 28 + len(strings) * 4, 0)
    pool += struct.pack("<" + "I" * len(offsets), *offsets) + data
    resources = struct.pack("<HHI", 0x180, 8, 8 + 4 * len(attrs)) + struct.pack("<" + "I" * len(attrs), *attrs.values())
    uri, prefix = indexes["http://schemas.android.com/apk/res/android"], indexes["android"]

    def node(kind, body):
        return struct.pack("<HHIII", kind, 16, 16 + len(body), 1, NO_INDEX) + body

    def start(name, values):
        body = struct.pack("<II6H", NO_INDEX, indexes[name], 20, 20, len(values), 0, 0, 0)
        for attr, value, datatype in values:
            ns = NO_INDEX if attr == "package" else uri
            raw = indexes[value] if datatype == 3 else NO_INDEX
            value = indexes[value] if datatype == 3 else value
            body += struct.pack("<IIIHBBI", ns, indexes[attr], raw, 8, 0, datatype, value)
        return node(0x102, body)

    def end(name):
        return node(0x103, struct.pack("<II", NO_INDEX, indexes[name]))

    body = pool + resources + node(0x100, struct.pack("<II", prefix, uri))
    body += start("manifest", [("package", PACKAGE, 3), ("versionCode", 1, 0x10), ("versionName", "1.0", 3)])
    body += start("uses-sdk", [("minSdkVersion", 23, 0x10), ("targetSdkVersion", 28, 0x10)]) + end("uses-sdk")
    body += start("uses-permission", [("name", "android.permission.CAMERA", 3)]) + end("uses-permission")
    body += start("application", [("hasCode", 0, 0x12), ("label", "Droidtoolbox Backend Test", 3)])
    body += start("receiver", [("name", "android.content.BroadcastReceiver", 3), ("exported", 0, 0x12), ("enabled", 1, 0x12)])
    body += start("intent-filter", []) + start("action", [("name", PACKAGE + ".TEST", 3)]) + end("action") + end("intent-filter")
    body += end("receiver") + end("application")
    body += end("manifest") + node(0x101, struct.pack("<II", prefix, uri))
    return struct.pack("<HHI", 3, 8, len(body) + 8) + body


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--jdk", required=True)
    args = parser.parse_args()
    dest = Path(__file__).parent / "fixtures/apk/backend-test.apk"
    dest.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("AndroidManifest.xml", manifest())
    with tempfile.TemporaryDirectory() as tmp:
        key = str(Path(tmp) / "test.p12")
        common = {"capture_output": True, "check": True, "timeout": 30, "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
        subprocess.run([str(Path(args.jdk) / "bin/keytool.exe"), "-genkeypair", "-alias", "dtb", "-keystore", key,
                        "-storetype", "PKCS12", "-storepass", "dtb-test-only", "-keyalg", "RSA", "-keysize", "2048", "-validity", "36500",
                        "-dname", "CN=Droidtoolbox Test Only", "-noprompt"], **common)
        subprocess.run([str(Path(args.jdk) / "bin/jarsigner.exe"), "-keystore", key, "-storepass", "dtb-test-only",
                        "-sigalg", "SHA256withRSA", "-digestalg", "SHA-256", str(dest), "dtb"], **common)
    print(dest)


if __name__ == "__main__":
    main()
