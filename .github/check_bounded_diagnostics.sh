#!/usr/bin/env bash
set -euo pipefail

diagnostics_path=${1:?diagnostics path is required}
max_bytes=${2:?maximum diagnostics bytes are required}
[[ "$max_bytes" =~ ^[1-9][0-9]*$ ]] || exit 64
test -f "$diagnostics_path"
if [ "$(wc -c <"$diagnostics_path")" -gt "$max_bytes" ]; then
  echo "bounded command diagnostics exceed $max_bytes bytes" >&2
  exit 1
fi
if [ -s "$diagnostics_path" ]; then
  cat -- "$diagnostics_path" >&2
  if grep -Eiq '(^|[^[:alnum:]_])(warning|warn|deprecated|error|fatal|fail|failed|failure|denied|unauthorized)([^[:alnum:]_]|$)' "$diagnostics_path"; then
    echo "bounded command emitted warning or error diagnostics" >&2
    exit 1
  fi
fi
