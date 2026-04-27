#!/usr/bin/env bash
set -euo pipefail

seconds="${1:-3}"
code="${2:-1}"
echo "[sh_fail_after_sleep] start, sleeping ${seconds}s"
sleep "${seconds}"
echo "[sh_fail_after_sleep] failing with exit code=${code}"
exit "${code}"
