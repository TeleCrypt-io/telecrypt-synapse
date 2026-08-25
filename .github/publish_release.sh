#!/usr/bin/env bash
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GH_API_VERSION:?GH_API_VERSION is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${RELEASE_RECORD:?RELEASE_RECORD is required}"
: "${RELEASE_ASSET_NAME:?RELEASE_ASSET_NAME is required}"
: "${EXPECTED_TAG:?EXPECTED_TAG is required}"
: "${EXPECTED_SHA:?EXPECTED_SHA is required}"
: "${EXPECTED_ANNOTATED_TAG_SHA:?EXPECTED_ANNOTATED_TAG_SHA is required}"
: "${EXPECTED_DIGEST:?EXPECTED_DIGEST is required}"

readonly REPOSITORY='TeleCrypt-io/telecrypt-synapse'
readonly IMAGE='ghcr.io/telecrypt-io/telecrypt-synapse'
readonly RELEASE_BODY="Exact Synapse release for source commit $EXPECTED_SHA."
readonly MAX_RECORD_BYTES=$((64 * 1024))
readonly MAX_API_BYTES=$((1024 * 1024))
readonly MAX_COMMAND_BYTES=$((64 * 1024))
readonly MAX_ASSET_BYTES=$((64 * 1024))
readonly REQUIRED_API_VERSION='2026-03-10'

test "$GH_API_VERSION" = "$REQUIRED_API_VERSION"
test "$GITHUB_REPOSITORY" = "$REPOSITORY"
[[ "$EXPECTED_TAG" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)-tc[1-9][0-9]*$ ]]
[[ "$EXPECTED_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$EXPECTED_ANNOTATED_TAG_SHA" =~ ^[0-9a-f]{40}$ ]]
[[ "$EXPECTED_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
test "$RELEASE_ASSET_NAME" = "telecrypt-synapse-$EXPECTED_TAG.digest.json"

record_size="$(wc -c <"$RELEASE_RECORD")"
test "$record_size" -gt 0 -a "$record_size" -le "$MAX_RECORD_BYTES"
record_digest="sha256:$(sha256sum "$RELEASE_RECORD" | awk '{print $1}')"
jq -e -s 'length == 1 and (.[0] | type == "object" and
  (keys == ["annotated_tag_sha", "digest", "image", "schema_version", "source_commit", "tag"]))' \
  "$RELEASE_RECORD" >/dev/null
jq -e --arg tag "$EXPECTED_TAG" --arg digest "$EXPECTED_DIGEST" --arg source "$EXPECTED_SHA" \
  --arg tag_object "$EXPECTED_ANNOTATED_TAG_SHA" --arg image "$IMAGE" \
  'type == "object" and .image == $image and .tag == $tag and .digest == $digest and
   .source_commit == $source and .annotated_tag_sha == $tag_object and .schema_version == 1' \
  "$RELEASE_RECORD" >/dev/null
canonical_record="$(mktemp)"
release_json="$(mktemp)"
release_error="$(mktemp)"
downloaded_asset="$(mktemp)"
release_headers="$(mktemp)"
release_page_headers="$(mktemp)"
release_page_json="$(mktemp)"
release_matches="$(mktemp)"
release_id=''
cleanup() { rm -f -- "$canonical_record" "$release_json" "$release_error" "$downloaded_asset" "$release_headers" "$release_page_headers" "$release_page_json" "$release_matches" "$release_json.create.log" "$release_json.create.error" "$release_json.upload.log" "$release_json.upload.error" "$release_json.edit.log" "$release_json.edit.error"; }
trap cleanup EXIT
trap 'cleanup; exit 143' HUP INT TERM
jq -cS . "$RELEASE_RECORD" >"$canonical_record"
cmp "$RELEASE_RECORD" "$canonical_record"

bounded_command() {
  local max_bytes="$1" output="$2" stderr="$3" timeout_seconds="$4" status
  shift 4
  (( max_bytes > 0 && max_bytes % 1024 == 0 )) || return 64
  rm -f -- "$output" "$stderr"
  if /usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/bounded-command.py" \
    --stdout-limit "$max_bytes" --stderr-limit "$max_bytes" \
    --stdout-path "$output" --stderr-path "$stderr" --timeout "$timeout_seconds" -- "$@"; then
    status=0
  else
    status=$?
  fi
  if [[ "$status" -eq 0 && -s "$stderr" ]]; then
    cat -- "$stderr" >&2
    return 1
  fi
  return "$status"
}

http_status() {
  local count
  count="$(grep -Ec '^HTTP/[0-9.]+[[:space:]][0-9]{3}([[:space:]]|$)' "$1")"
  test "$count" -eq 1
  sed -n '1s/^HTTP\/[0-9.]*[[:space:]]\([0-9][0-9][0-9]\).*$/\1/p' "$1"
}

http_body() {
  awk 'BEGIN { body = 0 } { line = $0; sub(/\r$/, "", line); if (!body) { if (line == "") body = 1; next } print }' "$1"
}

discover_release_id() {
  local page page_size complete=0 command_status code match_count
  : >"$release_matches"
  for page in $(seq 1 10); do
    if bounded_command "$MAX_API_BYTES" "$release_page_headers" "$release_error" 30 \
      gh api --include --hostname github.com --header 'Accept: application/vnd.github+json' \
      --header "X-GitHub-Api-Version: $GH_API_VERSION" \
      "repos/$REPOSITORY/releases?per_page=100&page=$page"; then
      command_status=0
    else
      command_status=$?
    fi
    test "$command_status" -eq 0 || return 1
    test "$(wc -c <"$release_page_headers")" -le "$MAX_API_BYTES" || return 1
    test "$(wc -c <"$release_error")" -le "$MAX_API_BYTES" || return 1
    code="$(http_status "$release_page_headers")" || return 1
    test "$code" = 200 || return 1
    http_body "$release_page_headers" >"$release_page_json"
    test "$(wc -c <"$release_page_json")" -le "$MAX_API_BYTES" || return 1
    jq -e 'type == "array" and length <= 100 and all(.[]; type == "object" and (.tag_name | type == "string"))' \
      "$release_page_json" >/dev/null || return 1
    jq -c --arg tag "$EXPECTED_TAG" '.[] | select(.tag_name == $tag)' \
      "$release_page_json" >>"$release_matches" || return 1
    page_size="$(jq -er 'length' "$release_page_json")" || return 1
    if (( page_size < 100 )); then
      complete=1
      break
    fi
  done
  test "$complete" -eq 1 || return 1
  match_count="$(wc -l <"$release_matches")" || return 1
  case "$match_count" in
    0)
      return 4
      ;;
    1)
      release_id="$(jq -s -er '.[0].id | select(type == "number" and . > 0 and . == floor)' "$release_matches")"
      [[ "$release_id" =~ ^[1-9][0-9]*$ ]]
      ;;
    *)
      echo 'release list contains multiple exact tag matches' >&2
      return 1
      ;;
  esac
}

get_release_by_id() {
  local command_status code
  if bounded_command "$MAX_API_BYTES" "$release_headers" "$release_error" 30 \
    gh api --include --hostname github.com --header 'Accept: application/vnd.github+json' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    "repos/$REPOSITORY/releases/$release_id"; then
    command_status=0
  else
    command_status=$?
  fi
  test "$(wc -c <"$release_headers")" -le "$MAX_API_BYTES" || return 1
  test "$(wc -c <"$release_error")" -le "$MAX_API_BYTES" || return 1
  code="$(http_status "$release_headers")" || return 1
  case "$code" in
    200)
      test "$command_status" -eq 0 || return 1
      test ! -s "$release_error" || return 1
      http_body "$release_headers" >"$release_json" || return 1
      test "$(wc -c <"$release_json")" -le "$MAX_API_BYTES" || return 1
      jq -e --argjson release_id "$release_id" \
        'type == "object" and .id == $release_id' "$release_json" >/dev/null || return 1
      return 0
      ;;
    *)
      cat -- "$release_error" >&2
      return 1
      ;;
  esac
}

get_release() {
  discover_release_id || return $?
  get_release_by_id
}

check_draft() {
  jq -e --argjson release_id "$release_id" --argjson max_asset_bytes "$MAX_ASSET_BYTES" \
    --arg tag "$EXPECTED_TAG" --arg body "$RELEASE_BODY" --arg asset "$RELEASE_ASSET_NAME" '
    type == "object" and .id == $release_id and .tag_name == $tag and
    .name == $tag and .body == $body and .draft == true and .prerelease == false and
    (.assets|type == "array" and length <= 1) and
    ((.assets|length) == 0 or
      ((.assets|length) == 1 and .assets[0].name == $asset and
       .assets[0].state == "uploaded" and
       (.assets[0].id | type == "number" and . > 0 and . == floor) and
       (.assets[0].size | type == "number" and . > 0 and . == floor and . <= $max_asset_bytes)))
  ' "$release_json" >/dev/null
}

if get_release; then
  if ! check_draft; then
    echo 'pre-existing release does not match the exact recoverable draft contract' >&2
    exit 1
  fi
else
  status=$?
  if [[ "$status" -ne 4 ]]; then
    cat -- "$release_error" >&2
    printf 'release discovery failed (status %s)\n' "$status" >&2
    exit 1
  fi
  if bounded_command "$MAX_COMMAND_BYTES" "$release_json.create.log" "$release_json.create.error" 60 \
    gh api --include --hostname github.com --method POST \
      --header 'Accept: application/vnd.github+json' \
      --header "X-GitHub-Api-Version: $GH_API_VERSION" \
      --field "tag_name=$EXPECTED_TAG" --field "target_commitish=$EXPECTED_SHA" \
      --field "name=$EXPECTED_TAG" --field "body=$RELEASE_BODY" \
      --field draft=true --field prerelease=false \
      "repos/$REPOSITORY/releases"; then
    create_status=0
  else
    create_status=$?
  fi
  if ! get_release; then
    cat -- "$release_json.create.log" "$release_json.create.error" >&2
    echo 'release draft could not be read back after creation' >&2
    exit 1
  fi
  if [[ "$create_status" -ne 0 || -s "$release_json.create.error" ]]; then
    cat -- "$release_json.create.log" "$release_json.create.error" >&2
    printf 'release draft creation failed (status %s)\n' "$create_status" >&2
    exit 1
  fi
  if ! check_draft; then
    echo 'created release draft does not match the exact empty-draft contract' >&2
    exit 1
  fi
fi

asset_count="$(jq -er '.assets|length' "$release_json")"
if [[ "$asset_count" -eq 0 ]]; then
  test "$record_size" -le "$MAX_ASSET_BYTES"
  if bounded_command "$MAX_COMMAND_BYTES" "$release_json.upload.log" "$release_json.upload.error" 120 \
    gh api --include --hostname github.com --method POST \
      --header 'Accept: application/vnd.github+json' \
      --header "X-GitHub-Api-Version: $GH_API_VERSION" \
      --header 'Content-Type: application/octet-stream' --input "$RELEASE_RECORD" \
      "https://uploads.github.com/repos/$REPOSITORY/releases/$release_id/assets?name=$RELEASE_ASSET_NAME"; then
    upload_status=0
  else
    upload_status=$?
  fi
  if [[ "$upload_status" -ne 0 || -s "$release_json.upload.error" ]]; then
    cat -- "$release_json.upload.log" "$release_json.upload.error" >&2
    printf 'release asset upload failed (status %s)\n' "$upload_status" >&2
    exit 1
  fi
fi
if ! get_release; then
  cat -- "$release_error" >&2
  echo 'release draft could not be read back after asset upload' >&2
  exit 1
fi
if ! check_draft; then
  echo 'release draft differs from the exact pre-publication contract' >&2
  exit 1
fi
if ! jq -e '(.assets | length) == 1' "$release_json" >/dev/null; then
  echo 'release draft does not contain the exact uploaded asset' >&2
  exit 1
fi
if ! asset_id="$(jq -er --arg asset "$RELEASE_ASSET_NAME" \
  '.assets | select(length == 1) | .[0] | select(.name == $asset) | .id | select(type == "number" and . > 0 and . == floor)' "$release_json")"; then
  echo 'release draft asset has no valid numeric id' >&2
  exit 1
fi
if ! bounded_command "$MAX_ASSET_BYTES" "$downloaded_asset" "$release_error" 120 \
  gh api --hostname github.com --header 'Accept: application/octet-stream' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    "repos/$REPOSITORY/releases/assets/$asset_id"; then
  cat -- "$release_error" >&2
  echo 'release draft asset could not be downloaded for verification' >&2
  exit 1
fi
if ! test "$(wc -c <"$downloaded_asset")" -le "$MAX_ASSET_BYTES" \
  || ! cmp "$RELEASE_RECORD" "$downloaded_asset" \
  || ! test "$(wc -c <"$downloaded_asset")" = "$record_size" \
  || ! test "sha256:$(sha256sum "$downloaded_asset" | awk '{print $1}')" = "$record_digest"; then
  echo 'downloaded draft asset differs from the exact release record' >&2
  exit 1
fi
if bounded_command "$MAX_COMMAND_BYTES" "$release_json.edit.log" "$release_json.edit.error" 60 \
  gh api --include --hostname github.com --method PATCH \
    --header 'Accept: application/vnd.github+json' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    --field draft=false --field prerelease=false --field "name=$EXPECTED_TAG" \
    --field "body=$RELEASE_BODY" "repos/$REPOSITORY/releases/$release_id"; then
  edit_status=0
else
  edit_status=$?
fi
if ! get_release; then
  cat -- "$release_json.edit.log" "$release_json.edit.error" "$release_error" >&2
  printf 'published release could not be read back after PATCH (status %s)\n' "$edit_status" >&2
  exit 1
fi
numeric_release_id="$release_id"
jq -e --argjson release_id "$numeric_release_id" '.id == $release_id' "$release_json" >/dev/null

if ! env EXPECTED_TAG="$EXPECTED_TAG" RELEASE_ASSET_NAME="$RELEASE_ASSET_NAME" \
  RELEASE_BODY="$RELEASE_BODY" RECORD_DIGEST="$record_digest" RECORD_SIZE="$record_size" \
  PYTHONDONTWRITEBYTECODE=1 python3 .github/validate_release.py "$release_json"; then
  cat -- "$release_json.edit.log" "$release_json.edit.error" >&2
  printf 'release publication failed after PATCH (status %s)\n' "$edit_status" >&2
  exit 1
fi
if [[ "$edit_status" -ne 0 || -s "$release_json.edit.error" ]]; then
  cat -- "$release_json.edit.log" "$release_json.edit.error" >&2
  printf 'release PATCH transport returned status %s; exact immutable readback resolved the outcome\n' "$edit_status" >&2
fi
asset_id="$(jq -er '.assets[0].id | select(type == "number" and . > 0 and . == floor)' "$release_json")"
[[ "$asset_id" =~ ^[1-9][0-9]*$ ]]
if ! bounded_command "$MAX_ASSET_BYTES" "$downloaded_asset" "$release_error" 120 \
  gh api --hostname github.com --header 'Accept: application/octet-stream' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    "repos/$REPOSITORY/releases/assets/$asset_id"; then
  cat -- "$release_error" >&2
  echo 'immutable release asset could not be downloaded for final verification' >&2
  exit 1
fi
if ! test "$(wc -c <"$downloaded_asset")" -le "$MAX_ASSET_BYTES" \
  || ! cmp "$RELEASE_RECORD" "$downloaded_asset" \
  || ! test "$(wc -c <"$downloaded_asset")" = "$record_size" \
  || ! test "sha256:$(sha256sum "$downloaded_asset" | awk '{print $1}')" = "$record_digest"; then
  echo 'immutable release asset differs from the exact release record' >&2
  exit 1
fi
