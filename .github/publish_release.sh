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
cleanup() { rm -f -- "$canonical_record" "$release_json" "$release_error" "$downloaded_asset" "$release_headers" "$release_json.create.log" "$release_json.create.error" "$release_json.upload.log" "$release_json.upload.error" "$release_json.edit.log" "$release_json.edit.error"; }
trap cleanup EXIT
trap 'cleanup; exit 143' HUP INT TERM
jq -cS . "$RELEASE_RECORD" >"$canonical_record"
cmp "$RELEASE_RECORD" "$canonical_record"

bounded_command() {
  local max_bytes="$1" output="$2" stderr="$3" timeout_seconds="$4" status
  shift 4
  (( max_bytes > 0 && max_bytes % 1024 == 0 )) || return 64
  rm -f -- "$output" "$stderr"
  set +e
  /usr/bin/python3 "$(dirname -- "${BASH_SOURCE[0]}")/bounded-command.py" \
    --stdout-limit "$max_bytes" --stderr-limit "$max_bytes" \
    --stdout-path "$output" --stderr-path "$stderr" --timeout "$timeout_seconds" -- "$@"
  status=$?
  set -e
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

get_release() {
  local command_status code
  set +e
  bounded_command "$MAX_API_BYTES" "$release_headers" "$release_error" 30 \
    gh api --include --hostname github.com --header 'Accept: application/vnd.github+json' \
    --header "X-GitHub-Api-Version: $GH_API_VERSION" \
    "repos/$REPOSITORY/releases/tags/$EXPECTED_TAG"
  command_status=$?
  set -e
  test "$(wc -c <"$release_headers")" -le "$MAX_API_BYTES"
  test "$(wc -c <"$release_error")" -le "$MAX_API_BYTES"
  code="$(http_status "$release_headers")"
  case "$code" in
    200)
      test "$command_status" -eq 0
      test ! -s "$release_error"
      http_body "$release_headers" >"$release_json"
      test "$(wc -c <"$release_json")" -le "$MAX_API_BYTES"
      return 0
      ;;
    404)
      test "$command_status" -ne 0
      return 4
      ;;
    *)
      cat -- "$release_error" >&2
      return 1
      ;;
  esac
}

check_draft() {
  jq -e --arg tag "$EXPECTED_TAG" --arg body "$RELEASE_BODY" --arg asset "$RELEASE_ASSET_NAME" '
    type == "object" and (.id|type == "number") and .tag_name == $tag and
    .name == $tag and .body == $body and .draft == true and .prerelease == false and
    (.assets|type == "array" and length <= 1) and
    ((.assets|length) == 0 or
      ((.assets|length) == 1 and .assets[0].name == $asset and
       .assets[0].state == "uploaded" and (.assets[0].id|type == "number") and
       (.assets[0].size|type == "number")))
  ' "$release_json" >/dev/null
}

if get_release; then
  check_draft
else
  status=$?
  test "$status" = 4
  set +e
  bounded_command "$MAX_COMMAND_BYTES" "$release_json.create.log" "$release_json.create.error" 60 \
  gh release create "$EXPECTED_TAG" --repo "github.com/$REPOSITORY" --draft \
    --verify-tag --title "$EXPECTED_TAG" --notes "$RELEASE_BODY"
  set -e
  # A client timeout can follow a successful server-side create. Re-fetch and continue only
  # when the exact draft is present; a final release or an ambiguous response fails closed.
  get_release || exit 1
  test ! -s "$release_json.create.error"
  check_draft
fi

asset_count="$(jq -er '.assets|length' "$release_json")"
if [[ "$asset_count" -eq 0 ]]; then
  test "$record_size" -le "$MAX_ASSET_BYTES"
  set +e
  bounded_command "$MAX_COMMAND_BYTES" "$release_json.upload.log" "$release_json.upload.error" 120 \
    gh release upload "$EXPECTED_TAG" "$RELEASE_RECORD" --repo "github.com/$REPOSITORY"
  upload_status=$?
  set -e
  test ! -s "$release_json.upload.error"
  get_release
fi
get_release
check_draft
asset_id="$(jq -er --arg asset "$RELEASE_ASSET_NAME" \
  '.assets | select(length == 1) | .[0] | select(.name == $asset) | .id' "$release_json")"
bounded_command "$MAX_ASSET_BYTES" "$downloaded_asset" "$release_error" 120 \
gh api --hostname github.com --header 'Accept: application/octet-stream' \
  --header "X-GitHub-Api-Version: $GH_API_VERSION" \
  "repos/$REPOSITORY/releases/assets/$asset_id"
test "$(wc -c <"$downloaded_asset")" -le "$MAX_ASSET_BYTES"
cmp "$RELEASE_RECORD" "$downloaded_asset"
test "$(wc -c <"$downloaded_asset")" = "$record_size"
test "sha256:$(sha256sum "$downloaded_asset" | awk '{print $1}')" = "$record_digest"
if [[ "${upload_status:-0}" -ne 0 ]]; then
  test ! -s "$release_json.upload.error"
fi

set +e
bounded_command "$MAX_COMMAND_BYTES" "$release_json.edit.log" "$release_json.edit.error" 60 \
gh release edit "$EXPECTED_TAG" --repo "github.com/$REPOSITORY" \
  --draft=false --verify-tag --title "$EXPECTED_TAG" --notes "$RELEASE_BODY"
edit_status=$?
set -e
test ! -s "$release_json.edit.error"
get_release

EXPECTED_TAG="$EXPECTED_TAG" RELEASE_ASSET_NAME="$RELEASE_ASSET_NAME" \
  RELEASE_BODY="$RELEASE_BODY" RECORD_DIGEST="$record_digest" RECORD_SIZE="$record_size" \
  PYTHONDONTWRITEBYTECODE=1 \
  python3 .github/validate_release.py "$release_json"
asset_id="$(jq -er '.assets[0].id' "$release_json")"
bounded_command "$MAX_ASSET_BYTES" "$downloaded_asset" "$release_error" 120 \
gh api --hostname github.com --header 'Accept: application/octet-stream' \
  --header "X-GitHub-Api-Version: $GH_API_VERSION" \
  "repos/$REPOSITORY/releases/assets/$asset_id"
test "$(wc -c <"$downloaded_asset")" -le "$MAX_ASSET_BYTES"
cmp "$RELEASE_RECORD" "$downloaded_asset"
test "$(wc -c <"$downloaded_asset")" = "$record_size"
test "sha256:$(sha256sum "$downloaded_asset" | awk '{print $1}')" = "$record_digest"
if [[ "${edit_status:-0}" -ne 0 ]]; then
  test ! -s "$release_json.edit.error"
fi
