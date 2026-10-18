"""可选持续负载检查：python tests/stress_logcat.py（默认 5000 行/秒 × 60 秒）。"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import tracemalloc

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backend.adb import AdbBackend
from tests.fakes import FakeProcess, FakeRunner, fake_backend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--rate", type=int, default=5000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runner, process = FakeRunner(), FakeProcess()
    runner.add(("get-state",), (0, "device\n", ""))
    runner.processes.append(process)
    backend = fake_backend(AdbBackend, runner)
    done = threading.Event()
    stats = {"received": 0, "callbacks": 0, "max_batch": 0}
    errors = []

    def receive(lines):
        for line in lines:
            assert line == f"stress {stats['received']}"
            stats["received"] += 1
        stats["callbacks"] += 1
        stats["max_batch"] = max(stats["max_batch"], len(lines))

    tracemalloc.start()
    handle = backend.start_logcat("s", ["main"], receive, lambda error: (errors.append(error), done.set()))
    started = time.monotonic()
    total = args.rate * args.seconds
    batch_size = min(500, args.rate)
    for first in range(0, total, batch_size):
        end = min(first + batch_size, total)
        process.stdout.feed("".join(f"stress {i}\n" for i in range(first, end)))
        time.sleep(max(0, started + end / args.rate - time.monotonic()))
    deadline = time.monotonic() + 3
    while stats["received"] < total and time.monotonic() < deadline:
        time.sleep(.02)
    handle.stop()
    assert done.wait(3) and errors == [None]
    assert stats["received"] == total and stats["max_batch"] <= 500
    stats.update(seconds=round(time.monotonic() - started, 3), peak_bytes=tracemalloc.get_traced_memory()[1])
    tracemalloc.stop()
    rendered = json.dumps(stats, indent=2)
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
