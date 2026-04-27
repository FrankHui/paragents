#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser(description="Wait for user input from stdin.")
    parser.add_argument("--timeout", type=float, default=0.0, help="Optional pre-input sleep seconds.")
    parser.add_argument("--prompt", default="Type YES to continue: ", help="Prompt text.")
    args = parser.parse_args()

    if args.timeout > 0:
        print(f"[py_prompt_input] sleeping {args.timeout}s before prompt")
        time.sleep(args.timeout)

    sys.stdout.write(args.prompt)
    sys.stdout.flush()
    answer = sys.stdin.readline().strip()

    if answer.upper() == "YES":
        print("[py_prompt_input] accepted")
        return 0

    print(f"[py_prompt_input] rejected input={answer!r}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
