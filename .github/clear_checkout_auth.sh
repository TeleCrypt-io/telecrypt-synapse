#!/usr/bin/env bash
set -euo pipefail

unset_all_if_present() {
  local key="$1" status
  if git config --local --unset-all "$key"; then
    return 0
  fi
  status=$?
  # git-config documents status 5 as the expected missing-key result.
  [[ "$status" -eq 5 ]] || return "$status"
}

include_keys=''
if include_keys="$(git config --local --name-only --get-regexp '^includeif\.gitdir:')"; then
  :
else
  status=$?
  # git-config documents status 1 as the expected no-match result.
  [[ "$status" -eq 1 ]] || exit "$status"
fi

while IFS= read -r key; do
  [[ -n "$key" ]] || continue
  unset_all_if_present "$key"
done <<<"$include_keys"

unset_all_if_present 'http.https://github.com/.extraheader'
