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
max_manifest_bytes=$((1024 * 1024))
docker_timeout=90s
workdir=$(mktemp -d)
cleanup() { rm -rf -- "$workdir"; }
trap cleanup EXIT
trap 'cleanup; exit 143' HUP INT TERM

capture_command() {
  local max_stdout="$1" max_stderr="$2" stdout_path="$3" stderr_path="$4" timeout_value="$5" status
  shift 5
  set +e
  /usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/bounded-command.py" \
    --stdout-limit "$max_stdout" --stderr-limit "$max_stderr" \
    --stdout-path "$stdout_path" --stderr-path "$stderr_path" \
    --timeout "${timeout_value%s}" -- "$@"
  status=$?
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
  if ! capture_command "$max_manifest_bytes" $((64 * 1024)) "$path" "$stderr_path" "$docker_timeout" \
    docker manifest inspect --verbose "$image_ref"; then
    cat -- "$stderr_path" >&2
    echo "could not inspect the single registry manifest: $image_ref" >&2
    exit 1
  fi
  if [ "$(wc -c <"$path")" -gt "$max_manifest_bytes" ]; then
    echo "registry manifest exceeds $max_manifest_bytes bytes: $image_ref" >&2
    exit 1
  fi
  if [ "$(wc -c <"$stderr_path")" -gt 65536 ]; then
    echo "registry manifest diagnostics exceed 65536 bytes: $image_ref" >&2
    exit 1
  fi
  if ! bash "$(dirname -- "${BASH_SOURCE[0]}")/check_bounded_diagnostics.sh" "$stderr_path" $((64 * 1024)); then
    echo "registry manifest inspection emitted warning or error diagnostics: $image_ref" >&2
    exit 1
  fi
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

bounded_inspect() {
  local format=$1
  local stdout_path="$workdir/inspect.stdout"
  local stderr_path="$workdir/inspect.stderr"
  rm -f -- "$stdout_path" "$stderr_path"
  if ! capture_command $((64 * 1024)) $((64 * 1024)) "$stdout_path" "$stderr_path" 30s \
    docker image inspect "$image_ref" --format "$format"; then
    cat -- "$stderr_path" >&2
    return 1
  fi
  if [ "$(wc -c <"$stdout_path")" -gt 65536 ] || [ "$(wc -c <"$stderr_path")" -gt 65536 ]; then
    return 1
  fi
  if ! bash "$(dirname -- "${BASH_SOURCE[0]}")/check_bounded_diagnostics.sh" "$stderr_path" $((64 * 1024)); then
    return 1
  fi
  cat -- "$stdout_path"
}

initial_manifest="$workdir/initial.json"
capture_manifest "$initial_manifest"
if ! initial_pair=$(parse_manifest "$initial_manifest"); then
  echo "registry reference is not one linux/amd64 image manifest: $image_ref" >&2
  exit 1
fi
IFS=$'\t' read -r initial_digest initial_config_digest <<<"$initial_pair"
if [ "$initial_digest" != "$expected_manifest_digest" ]; then
  echo "registry manifest digest differs from the expected manifest $expected_manifest_digest: $image_ref" >&2
  exit 1
fi
if [ "$initial_config_digest" != "$expected_image_id" ]; then
  echo "registry config digest differs from the tested image ID: $image_ref" >&2
  exit 1
fi

if ! capture_command $((64 * 1024)) $((64 * 1024)) "$workdir/pull.log" "$workdir/pull.stderr" "$docker_timeout" \
  docker pull --platform linux/amd64 "$image_ref"; then
  cat -- "$workdir/pull.stderr" >&2
  echo "could not pull the exact registry image: $image_ref" >&2
  exit 1
fi
if [ "$(wc -c <"$workdir/pull.log")" -gt 65536 ] || [ "$(wc -c <"$workdir/pull.stderr")" -gt 65536 ]; then
  echo "registry pull output exceeds 65536 bytes: $image_ref" >&2
  exit 1
fi
if ! bash "$(dirname -- "${BASH_SOURCE[0]}")/check_bounded_diagnostics.sh" "$workdir/pull.stderr" $((64 * 1024)); then
  echo "registry pull emitted warning or error diagnostics: $image_ref" >&2
  exit 1
fi
if ! pulled_image_id=$(bounded_inspect '{{.Id}}'); then
  echo "could not inspect the pulled image: $image_ref" >&2
  exit 1
fi
if [ "$pulled_image_id" != "$expected_image_id" ]; then
  echo "pulled image ID differs from the tested image: $image_ref" >&2
  exit 1
fi
if [ "$(bounded_inspect '{{.Os}}/{{.Architecture}}')" != "linux/amd64" ]; then
  echo "pulled image is not linux/amd64: $image_ref" >&2
  exit 1
fi
if ! repo_digests=$(bounded_inspect '{{range .RepoDigests}}{{println .}}{{end}}'); then
  echo "could not inspect pulled image RepoDigests: $image_ref" >&2
  exit 1
fi
if ! grep -Fqx "$image_name@$initial_digest" <<<"$repo_digests"; then
  echo "pulled image RepoDigests do not bind to its inspected manifest: $image_ref" >&2
  exit 1
fi

post_manifest="$workdir/post.json"
capture_manifest "$post_manifest"
if ! post_pair=$(parse_manifest "$post_manifest"); then
  echo "registry reference changed to a non-single image manifest: $image_ref" >&2
  exit 1
fi
IFS=$'\t' read -r post_digest post_config_digest <<<"$post_pair"
if [ "$post_digest" != "$initial_digest" ] || [ "$post_config_digest" != "$initial_config_digest" ]; then
  echo "registry image changed while it was being pulled: $image_ref" >&2
  exit 1
fi
if [ "$post_digest" != "$expected_manifest_digest" ]; then
  echo "registry manifest digest after pull differs from the expected manifest $expected_manifest_digest: $image_ref" >&2
  exit 1
fi
if [ -n "$expected_digest" ] && [ "$post_digest" != "$expected_digest" ]; then
  echo "registry digest after pull differs from expected $expected_digest: $image_ref" >&2
  exit 1
fi
printf 'digest=%s\n' "$post_digest"
