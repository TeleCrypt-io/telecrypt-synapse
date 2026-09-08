#!/usr/bin/env bash
set -euo pipefail

stdout_path="${1:?stdout path is required}"
stderr_path="${2:?stderr path is required}"
timeout_seconds="${3:?timeout seconds are required}"
shift 3
[[ "$timeout_seconds" =~ ^[1-9][0-9]*$ ]] || exit 64
[[ $# -gt 0 ]] || exit 64

command_pid=''
on_signal() {
  local signal_name="$1" exit_status=143 probe_status=0 kill_status=0 wait_status=0 replay_status=0
  case "$signal_name" in
    HUP) exit_status=129 ;;
    INT) exit_status=130 ;;
  esac
  set +e
  trap - HUP INT TERM
  if [[ -n "$command_pid" ]]; then
    kill -0 "$command_pid" 2>/dev/null
    probe_status=$?
    if [[ "$probe_status" -eq 0 ]]; then
      kill -"$signal_name" "$command_pid"
      kill_status=$?
      if [[ "$kill_status" -ne 0 ]]; then
        printf 'ERROR: command child termination failed during %s (status %s)\n' "$signal_name" "$kill_status" >&2
      fi
    elif [[ "$probe_status" -ne 1 ]]; then
      printf 'ERROR: command child liveness check failed during %s (status %s)\n' "$signal_name" "$probe_status" >&2
    fi
    wait "$command_pid"
    wait_status=$?
    if [[ "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" ]]; then
      printf 'ERROR: command child wait failed during %s (status %s)\n' "$signal_name" "$wait_status" >&2
    fi
  fi
  if ! cat -- "$stdout_path" "$stderr_path" >&2; then
    echo "ERROR: could not replay complete command diagnostics" >&2
    replay_status=1
  fi
  if [[ "$probe_status" -gt 1 || "$kill_status" -ne 0 || "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" || "$replay_status" -ne 0 ]]; then
    exit_status=1
  fi
  exit "$exit_status"
}
trap 'on_signal HUP' HUP
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM

set +e
timeout --signal=TERM --kill-after=5s "${timeout_seconds}s" "$@" \
  >"$stdout_path" 2>"$stderr_path" &
command_pid=$!
wait "$command_pid"
status=$?
set -e
exit "$status"
