#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Sleep for N seconds, then succeed.")
    parser.add_argument("--seconds", type=float, default=5.0, help="How long to sleep.")
    parser.add_argument("--message", default="python sleep success", help="Final success message.")
    args = parser.parse_args()

    print(f"[py_sleep_success] start, sleeping {args.seconds}s")
    time.sleep(max(0.0, args.seconds))
    print(f"[py_sleep_success] done: {args.message}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
