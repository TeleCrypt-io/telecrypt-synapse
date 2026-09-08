#!/usr/bin/env bash
set -euo pipefail

archive="${1:?image archive path is required}"
tmp_root="${RUNNER_TEMP:-${TMPDIR:-/tmp}}"
stdout_file="$(mktemp "$tmp_root/docker-load.stdout.XXXXXX")"
stderr_file="$(mktemp "$tmp_root/docker-load.stderr.XXXXXX")"
cleanup() {
  local cleanup_status=0
  if ! rm -f -- "$stdout_file" "$stderr_file"; then
    echo "ERROR: could not remove docker load diagnostic files" >&2
    cleanup_status=1
  fi
  return "$cleanup_status"
}
on_exit() {
  local status=$? cleanup_status
  set +e
  cleanup
  cleanup_status=$?
  if [ "$status" -eq 0 ] && [ "$cleanup_status" -ne 0 ]; then
    return "$cleanup_status"
  fi
  return "$status"
}
trap on_exit EXIT
command_pid=''
on_signal() {
  local signal_name="$1" exit_status=143 probe_status=0 kill_status=0 wait_status=0 replay_status=0 cleanup_status=0
  case "$signal_name" in
    HUP) exit_status=129 ;;
    INT) exit_status=130 ;;
  esac
  set +e
  trap - EXIT HUP INT TERM
  if [[ -n "$command_pid" ]]; then
    kill -0 "$command_pid" 2>/dev/null
    probe_status=$?
    if [[ "$probe_status" -eq 0 ]]; then
      kill -"$signal_name" "$command_pid"
      kill_status=$?
      if [[ "$kill_status" -ne 0 ]]; then
        echo "ERROR: docker load child termination failed during $signal_name (status $kill_status)" >&2
      fi
    elif [[ "$probe_status" -ne 1 ]]; then
      echo "ERROR: docker load child liveness check failed during $signal_name (status $probe_status)" >&2
    fi
    wait "$command_pid"
    wait_status=$?
    if [[ "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" ]]; then
      echo "ERROR: docker load child wait failed during $signal_name (status $wait_status)" >&2
    fi
  fi
  if ! cat -- "$stdout_file" "$stderr_file" >&2; then
    echo "ERROR: could not replay complete docker load diagnostics" >&2
    replay_status=1
  fi
  cleanup || cleanup_status=$?
  if [[ "$probe_status" -gt 1 || "$kill_status" -ne 0 || "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" || "$replay_status" -ne 0 || "$cleanup_status" -ne 0 ]]; then
    exit_status=1
  fi
  exit "$exit_status"
}
trap 'on_signal HUP' HUP
trap 'on_signal INT' INT
trap 'on_signal TERM' TERM
set +e
timeout --signal=TERM --kill-after=5s 300s docker load --input "$archive" \
  >"$stdout_file" 2>"$stderr_file" &
command_pid=$!
wait "$command_pid"
status=$?
set -e
if [ "$status" -ne 0 ]; then
  cat -- "$stdout_file" "$stderr_file" >&2
  exit "$status"
fi
bash "$(dirname -- "${BASH_SOURCE[0]}")/check_diagnostics.sh" "$stderr_file"
cat -- "$stdout_file"
