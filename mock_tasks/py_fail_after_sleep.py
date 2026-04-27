#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Sleep then fail with non-zero exit.")
    parser.add_argument("--seconds", type=float, default=3.0, help="How long to sleep before failing.")
    parser.add_argument("--code", type=int, default=1, help="Exit code.")
    args = parser.parse_args()

    print(f"[py_fail_after_sleep] start, sleeping {args.seconds}s")
    time.sleep(max(0.0, args.seconds))
    print(f"[py_fail_after_sleep] failing with exit code={args.code}")
    return args.code


if __name__ == "__main__":
    raise SystemExit(main())
