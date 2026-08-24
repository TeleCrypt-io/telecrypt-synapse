#!/usr/bin/env bash
set -euo pipefail

archive="${1:?image archive path is required}"
tmp_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
stdout_file="$(mktemp "$tmp_root/docker-load.stdout.XXXXXX")"
stderr_file="$(mktemp "$tmp_root/docker-load.stderr.XXXXXX")"
cleanup() { rm -f -- "$stdout_file" "$stderr_file"; }
trap cleanup EXIT
trap 'cleanup; exit 143' HUP INT TERM
set +e
/usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/bounded-command.py" \
  --stdout-limit $((64 * 1024)) --stderr-limit $((64 * 1024)) \
  --stdout-path "$stdout_file" --stderr-path "$stderr_file" --timeout 300 -- \
  docker load --input "$archive"
status=$?
set -e
if [ "$status" -ne 0 ] || [ -s "$stderr_file" ]; then
  cat -- "$stderr_file" >&2
  exit 1
fi
