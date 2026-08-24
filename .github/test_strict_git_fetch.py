#!/usr/bin/env python3
"""Offline tests for bounded, sanitized Git fetch diagnostics."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parent / "strict_git_fetch.sh"


def write_fake_git(path: Path) -> None:
    path.write_text(
        """#!/bin/sh
set -eu
if [ "${1:-}" = config ]; then
  exit 0
fi
if [ "${FAKE_GIT_MODE:-}" = overflow ]; then
  yes SECRET_OVERFLOW | head -c 131072 >&2
  exit 0
fi
if [ "${FAKE_GIT_MODE:-}" = successful-stderr ]; then
  printf 'SECRET_SUCCESS_STDERR\\n' >&2
  exit 0
fi
if [ "${FAKE_GIT_MODE:-}" = environment-check ] && [ "${1:-}" = -c ]; then
  test "${GIT_CONFIG_COUNT:-}" = 0
  test "${GIT_CONFIG_PARAMETERS+x}" != x
  test "${GIT_CONFIG_KEY_0+x}" != x
  test "${GIT_CONFIG_VALUE_0+x}" != x
  for variable in \
    GIT_ASKPASS SSH_ASKPASS GIT_SSH GIT_SSH_COMMAND GIT_PROXY_COMMAND \
    SSH_AUTH_SOCK SSH_AGENT_PID HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy \
    https_proxy all_proxy NO_PROXY no_proxy GIT_HTTP_PROXY_AUTHMETHOD \
    GIT_SSL_NO_VERIFY GIT_SSL_VERSION GIT_SSL_CIPHER_LIST GIT_SSL_CAINFO \
    GIT_SSL_CAPATH GIT_SSL_CERT GIT_SSL_KEY CURL_CA_BUNDLE SSL_CERT_FILE \
    SSL_CERT_DIR REQUESTS_CA_BUNDLE NODE_EXTRA_CA_CERTS AWS_CA_BUNDLE \
    GIT_ALLOW_PROTOCOL; do
    eval 'test "${'"$variable"'+x}" != x'
  done
  test "$2" = protocol.version=2
  test "$4" = protocol.allow=never
  test "$6" = protocol.https.allow=always
  test "$8" = credential.helper=
  test "${10}" = credential.useHttpPath=false
  test "${12}" = http.sslVerify=true
  test "${13}" = fetch
  test "${17}" = https://github.com/TeleCrypt-io/telecrypt-synapse.git
  exit 0
fi
exit 0
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class StrictGitFetchTests(unittest.TestCase):
    def run_helper(self, mode: str) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_git = root / "git"
            write_fake_git(fake_git)
            return subprocess.run(
                ["bash", str(HELPER), "refs/heads/main:refs/remotes/origin/main"],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "FAKE_GIT_MODE": mode,
                },
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

    def test_overflow_fails_without_leaking_captured_output(self) -> None:
        result = self.run_helper("overflow")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("SECRET_OVERFLOW", result.stderr)

    def test_successful_stderr_fails_without_leaking_captured_output(self) -> None:
        result = self.run_helper("successful-stderr")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("git fetch emitted unexpected diagnostics", result.stderr)
        self.assertNotIn("SECRET_SUCCESS_STDERR", result.stderr)

    def test_fetch_uses_canonical_https_and_clears_ambient_transport_controls(self) -> None:
        result = self.run_helper_with_environment(
            "environment-check",
            {
                "GIT_CONFIG_COUNT": "1",
                "GIT_CONFIG_KEY_0": "credential.helper",
                "GIT_CONFIG_VALUE_0": "!printf hostile",
                "GIT_CONFIG_PARAMETERS": "'credential.helper=!printf hostile'",
                "GIT_ASKPASS": "/tmp/askpass",
                "SSH_ASKPASS": "/tmp/ssh-askpass",
                "GIT_SSH": "/tmp/ssh",
                "GIT_SSH_COMMAND": "ssh -o ProxyCommand=hostile",
                "GIT_PROXY_COMMAND": "/tmp/proxy",
                "SSH_AUTH_SOCK": "/tmp/agent.sock",
                "HTTP_PROXY": "http://proxy.invalid",
                "HTTPS_PROXY": "http://proxy.invalid",
                "ALL_PROXY": "http://proxy.invalid",
                "http_proxy": "http://proxy.invalid",
                "https_proxy": "http://proxy.invalid",
                "all_proxy": "http://proxy.invalid",
                "NO_PROXY": "*",
                "GIT_SSL_NO_VERIFY": "1",
                "GIT_SSL_VERSION": "SSLv3",
                "GIT_SSL_CAINFO": "/tmp/ca.pem",
                "CURL_CA_BUNDLE": "/tmp/ca.pem",
                "SSL_CERT_FILE": "/tmp/ca.pem",
                "GIT_ALLOW_PROTOCOL": "file:ssh",
            },
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_forbidden_local_and_worktree_configuration_is_rejected(self) -> None:
        forbidden = (
            ("url.https://evil.example/.insteadOf", "https://github.com/"),
            ("http.proxy", "http://evil.example"),
            ("credential.helper", "!echo hostile"),
            ("include.path", "/tmp/hostile-gitconfig"),
            ("core.sshCommand", "ssh -o ProxyCommand=hostile"),
            ("core.gitProxy", "git-proxy"),
            ("remote.origin.uploadpack", "hostile-upload-pack"),
            ("remote.origin.proxy", "hostile-proxy"),
        )
        for scope in ("--local", "--worktree"):
            for key, value in forbidden:
                with self.subTest(scope=scope, key=key):
                    with tempfile.TemporaryDirectory() as directory:
                        root = Path(directory)
                        subprocess.run(
                            ["git", "init", "-q", str(root)],
                            check=True,
                            capture_output=True,
                        )
                        if scope == "--worktree":
                            subprocess.run(
                                [
                                    "git",
                                    "-C",
                                    str(root),
                                    "config",
                                    "--local",
                                    "extensions.worktreeConfig",
                                    "true",
                                ],
                                check=True,
                            )
                        subprocess.run(
                            ["git", "-C", str(root), "config", scope, key, value],
                            check=True,
                            capture_output=True,
                        )
                        result = subprocess.run(
                            [
                                "bash",
                                str(HELPER),
                                "refs/heads/main:refs/remotes/origin/main",
                            ],
                            cwd=root,
                            env={**os.environ, "PATH": os.environ["PATH"]},
                            capture_output=True,
                            text=True,
                            timeout=10,
                            check=False,
                        )
                        self.assertNotEqual(result.returncode, 0)
                        self.assertIn("forbidden Git", result.stderr)

    def run_helper_with_environment(
        self, mode: str, overrides: dict[str, str]
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_git = root / "git"
            write_fake_git(fake_git)
            return subprocess.run(
                ["bash", str(HELPER), "refs/heads/main:refs/remotes/origin/main"],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": f"{root}:{os.environ['PATH']}",
                    "FAKE_GIT_MODE": mode,
                    **overrides,
                },
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )


if __name__ == "__main__":
    unittest.main()
