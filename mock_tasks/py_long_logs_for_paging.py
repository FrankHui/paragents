#!/usr/bin/env python3
"""
Generate many log lines for side-panel paging tests.
"""

from __future__ import annotations

import argparse
import sys
import time


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Emit many lines to test log pagination.")
    parser.add_argument("--lines", type=int, default=120, help="Total lines to print.")
    parser.add_argument("--delay-ms", type=int, default=15, help="Delay between lines in ms.")
    parser.add_argument(
        "--stderr-every",
        type=int,
        default=10,
        help="Every N lines print one line to stderr (0 disables).",
    )
    parser.add_argument(
        "--tail-sleep",
        type=float,
        default=1.5,
        help="Extra seconds to keep task running after logs are printed.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    lines = max(1, args.lines)
    delay_s = max(0, args.delay_ms) / 1000.0
    stderr_every = max(0, args.stderr_every)

    print("[paging-test] start")
    print(
        f"[paging-test] config lines={lines} delay_ms={args.delay_ms} "
        f"stderr_every={stderr_every} tail_sleep={args.tail_sleep}"
    )
    sys.stdout.flush()

    for i in range(1, lines + 1):
        print(f"[paging-test][stdout] line {i:04d} " + ("#" * ((i % 24) + 8)))
        if stderr_every and i % stderr_every == 0:
            print(f"[paging-test][stderr] checkpoint at line {i:04d}", file=sys.stderr)
        sys.stdout.flush()
        sys.stderr.flush()
        if delay_s:
            time.sleep(delay_s)

    if args.tail_sleep > 0:
        print(f"[paging-test] entering tail sleep {args.tail_sleep:.2f}s")
        sys.stdout.flush()
        time.sleep(args.tail_sleep)

    print("[paging-test] done")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
