"""winscope_proxy.py 启动垫片：python winscope_proxy_shim.py <winscope_proxy.py 路径> <端口>

附带的 winscope_proxy.py（6.0.2）把 --port 声明成字符串（没有 type=int），传入端口时 HTTPServer 绑定会报
TypeError，只有不传端口、使用默认 5544 才能运行。这里在不修改原脚本的前提下把解析结果里的 port 转成整数，
再按 __main__ 运行原脚本；原脚本修复后这个垫片也不影响其行为。
"""
import argparse
import runpy
import sys


def main():
    script, port = sys.argv[1], int(sys.argv[2])
    original = argparse.ArgumentParser.parse_args

    def parse_args(self, *args, **kwargs):
        ns = original(self, *args, **kwargs)
        if hasattr(ns, "port"):
            ns.port = int(ns.port)
        return ns

    argparse.ArgumentParser.parse_args = parse_args
    sys.argv = [script, "--port", str(port)]
    runpy.run_path(script, run_name="__main__")


if __name__ == "__main__":
    main()
