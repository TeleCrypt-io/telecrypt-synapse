#!/usr/bin/env bash
set -euo pipefail

: "${EXPECTED_TAG:?EXPECTED_TAG is required}"
: "${EXPECTED_SHA:?EXPECTED_SHA is required}"
: "${EXPECTED_ANNOTATED_TAG_SHA:?EXPECTED_ANNOTATED_TAG_SHA is required}"
context="${RECHECK_CONTEXT:-release}"
case "$context" in
  archive-upload|image-push|provenance|immutable-release|post-publication) ;;
  *) echo "unsupported release recheck context: $context" >&2; exit 2 ;;
esac

remote_tag_ref="refs/tags/$EXPECTED_TAG"
local_tag_ref="refs/tags/recheck-$context/$EXPECTED_TAG"
remote_main_ref="refs/remotes/origin/recheck-$context-main"
bash .github/strict_git_fetch.sh \
  "refs/heads/main:$remote_main_ref" \
  "$remote_tag_ref:$local_tag_ref"
if [ "$(bash .github/strict_git_fetch.sh local-read cat-file-type "$local_tag_ref" 2>/dev/null || true)" != "tag" ]; then
  echo "$remote_tag_ref must still be an annotated Git tag" >&2
  exit 2
fi
annotated_tag_sha="$(bash .github/strict_git_fetch.sh local-read rev-parse "$local_tag_ref")"
if [ "$annotated_tag_sha" != "$EXPECTED_ANNOTATED_TAG_SHA" ]; then
  echo "$remote_tag_ref changed before $context" >&2
  exit 2
fi
tagged_sha="$(bash .github/strict_git_fetch.sh local-read rev-parse "$local_tag_ref^{}")"
if [ "$tagged_sha" != "$EXPECTED_SHA" ]; then
  echo "$remote_tag_ref no longer points at the checked-out commit" >&2
  exit 2
fi
if ! bash .github/strict_git_fetch.sh local-ancestor "$tagged_sha" "$remote_main_ref"; then
  echo "$remote_tag_ref no longer points at a commit contained in refreshed origin/main" >&2
  exit 2
fi
