#!/usr/bin/env bash
set -euo pipefail

if (($# < 3 || $# > 4)); then
  echo "usage: verify_registry_image.sh IMAGE_REF EXPECTED_IMAGE_ID EXPECTED_MANIFEST_DIGEST [EXPECTED_DIGEST]" >&2
  exit 2
fi

image_ref=$1
expected_image_id=$2
expected_manifest_digest=$3
expected_digest=${4:-}
docker_timeout=90s
workdir=$(mktemp -d)
cleanup() {
  local cleanup_status=0
  if ! rm -rf -- "$workdir"; then
    echo "ERROR: could not remove registry verification diagnostics" >&2
    cleanup_status=1
  fi
  return "$cleanup_status"
}
on_exit() {
  local status=$? cleanup_status
  set +e
  cleanup
  cleanup_status=$?
  if [[ "$status" -eq 0 && "$cleanup_status" -ne 0 ]]; then
    return "$cleanup_status"
  fi
  return "$status"
}
trap on_exit EXIT
active_command_pid=''
on_signal() {
  local signal_name="$1" exit_status=143 probe_status=0 kill_status=0 wait_status=0 replay_status=0 cleanup_status=0
  case "$signal_name" in
    HUP) exit_status=129 ;;
    INT) exit_status=130 ;;
  esac
  set +e
  trap - EXIT HUP INT TERM
  if [[ -n "$active_command_pid" ]]; then
    kill -0 "$active_command_pid" 2>/dev/null
    probe_status=$?
    if [[ "$probe_status" -eq 0 ]]; then
      kill -"$signal_name" "$active_command_pid"
      kill_status=$?
      if [[ "$kill_status" -ne 0 ]]; then
        printf 'ERROR: registry verification child termination failed during %s (status %s)\n' "$signal_name" "$kill_status" >&2
      fi
    elif [[ "$probe_status" -ne 1 ]]; then
      printf 'ERROR: registry verification child liveness check failed during %s (status %s)\n' "$signal_name" "$probe_status" >&2
    fi
    wait "$active_command_pid"
    wait_status=$?
    if [[ "$wait_status" -ne 0 && "$wait_status" -ne "$exit_status" ]]; then
      printf 'ERROR: registry verification child wait failed during %s (status %s)\n' "$signal_name" "$wait_status" >&2
    fi
    active_command_pid=''
  fi
  if [[ -d "$workdir" ]]; then
    while IFS= read -r -d '' path; do
      cat -- "$path" >&2
      if [[ "$?" -ne 0 ]]; then
        echo "ERROR: could not replay registry verification diagnostics: $path" >&2
        replay_status=1
      fi
    done < <(find "$workdir" -type f -print0)
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

capture_command() {
  local stdout_path="$1" stderr_path="$2" timeout_value="$3" status
  shift 3
  set +e
  timeout --signal=TERM --kill-after=5s "${timeout_value}" "$@" \
    >"$stdout_path" 2>"$stderr_path" &
  active_command_pid=$!
  wait "$active_command_pid"
  status=$?
  active_command_pid=''
  set -e
  return "$status"
}

case "$image_ref" in
  *:*) image_name=${image_ref%:*} ;;
  *) echo "image reference must include a tag: $image_ref" >&2; exit 2 ;;
esac
if [[ ! "$expected_image_id" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "expected image ID is not an exact sha256 ID" >&2
  exit 2
fi
if [[ ! "$expected_manifest_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "expected manifest digest is not a sha256 digest" >&2
  exit 2
fi
if [ -n "$expected_digest" ] && [[ ! "$expected_digest" =~ ^sha256:[0-9a-f]{64}$ ]]; then
  echo "expected registry digest is not a sha256 digest" >&2
  exit 2
fi

capture_manifest() {
  local path=$1
  local stderr_path="$path.stderr"
  rm -f -- "$path" "$stderr_path"
  if ! capture_command "$path" "$stderr_path" "$docker_timeout" \
    docker manifest inspect --verbose "$image_ref"; then
    cat -- "$path" "$stderr_path" >&2
    echo "could not inspect the single registry manifest: $image_ref" >&2
    exit 1
  fi
  bash "$(dirname -- "${BASH_SOURCE[0]}")/check_diagnostics.sh" "$stderr_path"
}

parse_manifest() {
  jq -er '
    if type == "array" then
      if length == 1 then .[0]
      else error("registry tag must expose exactly one image manifest")
      end
    elif type == "object" then .
    else error("registry tag manifest response has an invalid shape")
    end
    | .Descriptor as $descriptor
    | ($descriptor.mediaType // "") as $media_type
    | ($descriptor.digest // "") as $digest
    | ($descriptor.platform // null) as $platform
    | (.SchemaV2Manifest.config.digest // .OCIManifest.config.digest // "") as $config_digest
    | (.SchemaV2Manifest.layers // .OCIManifest.layers // []) as $layers
    | select(
        ($media_type == "application/vnd.oci.image.manifest.v1+json" or
         $media_type == "application/vnd.docker.distribution.manifest.v2+json") and
        ($platform == null or
         ($platform | type == "object" and .os == "linux" and .architecture == "amd64")) and
        ($digest | test("^sha256:[0-9a-f]{64}$")) and
        ($config_digest | test("^sha256:[0-9a-f]{64}$")) and
        ($layers | type == "array" and length > 0 and all(.[];
          type == "object" and
          (.digest | type == "string" and test("^sha256:[0-9a-f]{64}$")) and
          (.size | type == "number" and . >= 0 and . == floor)
        ))
      )
    | [$digest, $config_digest]
    | @tsv
  ' "$1"
}

inspect_image() {
  local format=$1
  local stdout_path="$workdir/inspect.stdout"
  local stderr_path="$workdir/inspect.stderr"
  rm -f -- "$stdout_path" "$stderr_path"
  if ! capture_command "$stdout_path" "$stderr_path" 30s \
    docker image inspect "$image_ref" --format "$format"; then
    cat -- "$stdout_path" "$stderr_path" >&2
    return 1
  fi
  bash "$(dirname -- "${BASH_SOURCE[0]}")/check_diagnostics.sh" "$stderr_path"
  cat -- "$stdout_path"
}

initial_manifest="$workdir/initial.json"
capture_manifest "$initial_manifest"
if ! initial_pair=$(parse_manifest "$initial_manifest"); then
  cat -- "$initial_manifest" "$initial_manifest.stderr" >&2
  echo "registry reference is not one linux/amd64 image manifest: $image_ref" >&2
  exit 1
fi
IFS=$'\t' read -r initial_digest initial_config_digest <<<"$initial_pair"
if [ "$initial_digest" != "$expected_manifest_digest" ]; then
  cat -- "$initial_manifest" "$initial_manifest.stderr" >&2
  echo "registry manifest digest differs from the expected manifest $expected_manifest_digest: $image_ref" >&2
  exit 1
fi
if [ "$initial_config_digest" != "$expected_image_id" ]; then
  cat -- "$initial_manifest" "$initial_manifest.stderr" >&2
  echo "registry config digest differs from the tested image ID: $image_ref" >&2
  exit 1
fi

if ! capture_command "$workdir/pull.log" "$workdir/pull.stderr" "$docker_timeout" \
  docker pull --platform linux/amd64 "$image_ref"; then
  cat -- "$workdir/pull.log" "$workdir/pull.stderr" >&2
  echo "could not pull the exact registry image: $image_ref" >&2
  exit 1
fi
bash "$(dirname -- "${BASH_SOURCE[0]}")/check_diagnostics.sh" "$workdir/pull.stderr"
cat -- "$workdir/pull.log"
if ! pulled_image_id=$(inspect_image '{{.Id}}'); then
  echo "could not inspect the pulled image: $image_ref" >&2
  exit 1
fi
if [ "$pulled_image_id" != "$expected_image_id" ]; then
  cat -- "$workdir/inspect.stdout" "$workdir/inspect.stderr" >&2
  echo "pulled image ID differs from the tested image: $image_ref" >&2
  exit 1
fi
pulled_platform="$(inspect_image '{{.Os}}/{{.Architecture}}')"
if [ "$pulled_platform" != "linux/amd64" ]; then
  cat -- "$workdir/inspect.stdout" "$workdir/inspect.stderr" >&2
  echo "pulled image is not linux/amd64: $image_ref" >&2
  exit 1
fi
if ! repo_digests=$(inspect_image '{{range .RepoDigests}}{{println .}}{{end}}'); then
  echo "could not inspect pulled image RepoDigests: $image_ref" >&2
  exit 1
fi
if ! grep -Fqx "$image_name@$initial_digest" <<<"$repo_digests"; then
  cat -- "$workdir/inspect.stdout" "$workdir/inspect.stderr" >&2
  echo "pulled image RepoDigests do not bind to its inspected manifest: $image_ref" >&2
  exit 1
fi

post_manifest="$workdir/post.json"
capture_manifest "$post_manifest"
if ! post_pair=$(parse_manifest "$post_manifest"); then
  cat -- "$post_manifest" "$post_manifest.stderr" >&2
  echo "registry reference changed to a non-single image manifest: $image_ref" >&2
  exit 1
fi
IFS=$'\t' read -r post_digest post_config_digest <<<"$post_pair"
if [ "$post_digest" != "$initial_digest" ] || [ "$post_config_digest" != "$initial_config_digest" ]; then
  cat -- "$post_manifest" "$post_manifest.stderr" >&2
  echo "registry image changed while it was being pulled: $image_ref" >&2
  exit 1
fi
if [ "$post_digest" != "$expected_manifest_digest" ]; then
  cat -- "$post_manifest" "$post_manifest.stderr" >&2
  echo "registry manifest digest after pull differs from the expected manifest $expected_manifest_digest: $image_ref" >&2
  exit 1
fi
if [ -n "$expected_digest" ] && [ "$post_digest" != "$expected_digest" ]; then
  cat -- "$post_manifest" "$post_manifest.stderr" >&2
  echo "registry digest after pull differs from expected $expected_digest: $image_ref" >&2
  exit 1
fi
printf 'digest=%s\n' "$post_digest"
