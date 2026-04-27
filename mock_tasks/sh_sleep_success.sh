#!/usr/bin/env bash
set -euo pipefail

seconds="${1:-5}"
echo "[sh_sleep_success] start, sleeping ${seconds}s"
sleep "${seconds}"
echo "[sh_sleep_success] done"
