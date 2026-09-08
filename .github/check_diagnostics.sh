#!/usr/bin/env bash
set -euo pipefail

diagnostics_path="${1:?diagnostics path is required}"
if [[ -s "$diagnostics_path" ]]; then
  cat -- "$diagnostics_path" >&2
fi
