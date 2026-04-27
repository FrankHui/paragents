#!/usr/bin/env bash
set -euo pipefail

seconds="${1:-0}"
if [[ "${seconds}" != "0" ]]; then
  echo "[sh_prompt_input] sleeping ${seconds}s before prompt"
  sleep "${seconds}"
fi

read -r -p "Type YES to continue: " answer
if [[ "${answer}" == "YES" ]]; then
  echo "[sh_prompt_input] accepted"
  exit 0
fi

echo "[sh_prompt_input] rejected input='${answer}'"
exit 2
