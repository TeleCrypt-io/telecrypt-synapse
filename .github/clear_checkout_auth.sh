#!/usr/bin/env bash
set -euo pipefail

while IFS= read -r key; do
  [[ -n "$key" ]] || continue
  git config --local --unset-all "$key" || true
done < <(git config --local --name-only --get-regexp '^includeif\.gitdir:' || true)

git config --local --unset-all 'http.https://github.com/.extraheader' || true
