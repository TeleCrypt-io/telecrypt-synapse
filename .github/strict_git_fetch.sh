#!/usr/bin/env bash
set -euo pipefail

if (($# == 0)); then
  echo 'usage: strict_git_fetch.sh REFSPEC...' >&2
  exit 2
fi

readonly MAX_OUTPUT_BYTES=$((64 * 1024))
readonly FETCH_TIMEOUT_SECONDS=120
readonly CANONICAL_REMOTE_URL='https://github.com/TeleCrypt-io/telecrypt-synapse.git'
workdir="$(mktemp -d)"
trap 'rm -rf -- "$workdir"' EXIT

# Git accepts configuration through both environment variables and the local
# config files. Do not let inherited values influence either the inspection
# below or the network operation. A zero count disables GIT_CONFIG_KEY_n and
# GIT_CONFIG_VALUE_n; remove the parameters too so malformed inherited values
# cannot make Git reject the command before it reaches the explicit -c options.
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL=/dev/null
export GIT_CONFIG_COUNT=0 GIT_TERMINAL_PROMPT=0
unset GIT_CONFIG_PARAMETERS
config_parameter_names=()
while IFS= read -r config_parameter; do
  case "$config_parameter" in
    GIT_CONFIG_KEY_*|GIT_CONFIG_VALUE_*) config_parameter_names+=("$config_parameter") ;;
  esac
done < <(compgen -A variable)
if ((${#config_parameter_names[@]})); then
  unset "${config_parameter_names[@]}"
fi

# Git's HTTPS transport can still be redirected to askpass, SSH, a proxy, or
# an ambient CA/TLS override through the process environment. Clear the
# controls for all of those transports, plus tracing variables that could turn
# a failed fetch into an unbounded credential-bearing diagnostic.
unset \
  GIT_ASKPASS SSH_ASKPASS GIT_SSH GIT_SSH_COMMAND GIT_PROXY_COMMAND \
  SSH_AUTH_SOCK SSH_AGENT_PID SSH_CONNECTION SSH_CLIENT SSH_TTY \
  HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy \
  NO_PROXY no_proxy GIT_HTTP_PROXY_AUTHMETHOD \
  GIT_SSL_NO_VERIFY GIT_SSL_VERSION GIT_SSL_CIPHER_LIST GIT_SSL_CAINFO \
  GIT_SSL_CAPATH GIT_SSL_CERT GIT_SSL_KEY GIT_SSL_CERT_PASSWORD_PROTECTED \
  CURL_CA_BUNDLE SSL_CERT_FILE SSL_CERT_DIR REQUESTS_CA_BUNDLE \
  NODE_EXTRA_CA_CERTS AWS_CA_BUNDLE SSLKEYLOGFILE \
  GIT_ALLOW_PROTOCOL \
  GIT_TRACE GIT_TRACE_PACKET GIT_TRACE_CURL GIT_TRACE2 GIT_TRACE2_EVENT \
  GIT_TRACE2_PERF GIT_TRACE2_BRIEF

for config_scope in --local --worktree; do
  set +e
  config_keys="$(git config "$config_scope" --no-includes --name-only --get-regexp '.*' 2>/dev/null)"
  config_status=$?
  set -e
  if [[ "$config_status" -gt 1 ]]; then
    echo "could not inspect Git $config_scope configuration" >&2
    exit 1
  fi
  while IFS= read -r key; do
    [[ -n "$key" ]] || continue
    case "$key" in
      url.*|http.*|credential.*|include*|core.ssh*|core.gitproxy|\
      remote.*.uploadpack|remote.*.proxy)
        echo "forbidden Git $config_scope configuration key: $key" >&2
        exit 1
        ;;
    esac
  done <<<"$config_keys"
done

set +e
(
  # RLIMIT_FSIZE is inherited by git and its children, so neither captured
  # stream can grow past the diagnostic bound while the fetch is running.
  ulimit -f "$(((MAX_OUTPUT_BYTES + 511) / 512))"
  exec timeout --signal=TERM --kill-after=5s "${FETCH_TIMEOUT_SECONDS}s" \
    git \
      -c protocol.version=2 \
      -c protocol.allow=never \
      -c protocol.https.allow=always \
      -c credential.helper= \
      -c credential.useHttpPath=false \
      -c http.sslVerify=true \
      fetch --quiet --force --no-tags "$CANONICAL_REMOTE_URL" "$@"
) >"$workdir/stdout" 2>"$workdir/stderr"
status=$?
set -e

if [[ "$(wc -c <"$workdir/stdout")" -gt "$MAX_OUTPUT_BYTES" ||
      "$(wc -c <"$workdir/stderr")" -gt "$MAX_OUTPUT_BYTES" ]]; then
  echo "git fetch exceeded the diagnostic output limit" >&2
  exit 1
fi
if [[ "$status" -ne 0 ]]; then
  echo "git fetch failed (status $status)" >&2
  exit 1
fi
if [[ -s "$workdir/stdout" || -s "$workdir/stderr" ]]; then
  echo 'git fetch emitted unexpected diagnostics' >&2
  exit 1
fi
