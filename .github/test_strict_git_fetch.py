#!/usr/bin/env python3
"""Offline tests for the bounded, sanitized Git transport boundary."""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


HELPER = Path(__file__).resolve().parent / "strict_git_fetch.sh"


def init_repo() -> Path:
    root = Path(tempfile.mkdtemp(prefix="synapse-git-transport-"))
    result = subprocess.run(["/usr/bin/git", "init", "--quiet", str(root)], capture_output=True, text=True)
    if result.returncode:
        raise AssertionError(result.stderr)
    return root


def run_helper(root: Path, *arguments: str, **overrides: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ, **overrides}
    return subprocess.run(
        ["/bin/bash", str(HELPER), *arguments],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


class StrictGitFetchTests(unittest.TestCase):
    def test_static_boundary_uses_trusted_git_and_canonical_https(self) -> None:
        helper = HELPER.read_text(encoding="utf-8")
        for required in (
            "TRUSTED_GIT='/usr/bin/git'",
            "CANONICAL_URL='https://github.com/TeleCrypt-io/telecrypt-synapse.git'",
            "GIT_CONFIG_SYSTEM=/dev/null",
            "GIT_CONFIG_GLOBAL=/dev/null",
            "GIT_CONFIG_COUNT=0",
            "GIT_CONFIG_PARAMETERS=",
            "GIT_ASKPASS=",
            "GIT_SSH_COMMAND=",
            "HTTPS_PROXY=",
            "GIT_SSL_NO_VERIFY=",
            "GIT_DIR",
            "GIT_COMMON_DIR",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_INDEX_FILE",
            "GIT_NAMESPACE",
            "GIT_REPLACE_REF_BASE",
            "GIT_EXEC_PATH",
            "core.askpass",
            "protocol.version=2",
            "protocol.https.allow=always",
            "credential.helper=",
            "core.sshCommand=",
            "core.gitproxy=",
            "remote\\..*\\.(uploadpack|proxy)",
            "--no-includes",
            "local-read",
            "local-ancestor",
        ):
            self.assertIn(required, helper)

    def test_hostile_process_environment_is_cleared_for_local_checks(self) -> None:
        root = init_repo()
        try:
            result = run_helper(
                root,
                "check",
                GIT_CONFIG_COUNT="2",
                GIT_CONFIG_KEY_0="http.proxy",
                GIT_CONFIG_VALUE_0="http://evil.invalid",
                GIT_CONFIG_KEY_1="credential.helper",
                GIT_CONFIG_VALUE_1="!printf hostile",
                GIT_CONFIG_PARAMETERS="'http.proxy=http://evil.invalid'",
                GIT_DIR="/tmp/hostile-git-dir",
                GIT_COMMON_DIR="/tmp/hostile-common-dir",
                GIT_OBJECT_DIRECTORY="/tmp/hostile-objects",
                GIT_ALTERNATE_OBJECT_DIRECTORIES="/tmp/hostile-alternates",
                GIT_INDEX_FILE="/tmp/hostile-index",
                GIT_NAMESPACE="hostile",
                GIT_REPLACE_REF_BASE="refs/replace/hostile",
                GIT_EXEC_PATH="/tmp/hostile-exec",
                GIT_ASKPASS="/tmp/hostile-askpass",
                SSH_ASKPASS="/tmp/hostile-ssh-askpass",
                GIT_SSH_COMMAND="ssh -oProxyCommand=hostile",
                HTTPS_PROXY="http://secret.invalid",
                GIT_SSL_NO_VERIFY="1",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
        finally:
            subprocess.run(["rm", "-rf", str(root)], check=True)

    def test_option_shaped_refspec_is_rejected_before_transport(self) -> None:
        root = init_repo()
        try:
            result = run_helper(root, "--upload-pack=/tmp/secret-capture")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("unsupported operation", result.stderr)
        finally:
            subprocess.run(["rm", "-rf", str(root)], check=True)

    def test_forbidden_local_and_worktree_configuration_is_rejected(self) -> None:
        forbidden = (
            ("url.https://evil.example/.insteadOf", "https://github.com/"),
            ("http.proxy", "http://evil.example"),
            ("credential.helper", "!echo hostile"),
            ("include.path", "/tmp/hostile-gitconfig"),
            ("core.askPass", "/tmp/askpass"),
            ("core.sshCommand", "ssh -o ProxyCommand=hostile"),
            ("core.gitProxy", "git-proxy"),
            ("core.worktree", "/tmp/hostile-worktree"),
            ("remote.origin.pushurl", "https://evil.example/push.git"),
            ("remote.origin.vcs", "ssh"),
            ("remote.origin.uploadpack", "hostile-upload-pack"),
            ("remote.origin.proxy", "hostile-proxy"),
        )
        for scope in ("--local", "--worktree"):
            for key, value in forbidden:
                with self.subTest(scope=scope, key=key):
                    root = init_repo()
                    try:
                        if scope == "--worktree":
                            subprocess.run(
                                ["/usr/bin/git", "-C", str(root), "config", "--local",
                                 "extensions.worktreeConfig", "true"],
                                check=True,
                            )
                        result = subprocess.run(
                            ["/usr/bin/git", "-C", str(root), "config", scope, key, value],
                            capture_output=True,
                            text=True,
                        )
                        self.assertEqual(result.returncode, 0, result.stderr)
                        checked = run_helper(root, "check")
                        self.assertNotEqual(checked.returncode, 0)
                        self.assertIn("unsafe transport key", checked.stderr)
                    finally:
                        subprocess.run(["rm", "-rf", str(root)], check=True)

    def test_oversized_and_fifo_config_are_rejected_without_hanging(self) -> None:
        root = init_repo()
        try:
            config_path = root / ".git" / "config"
            with config_path.open("ab") as stream:
                stream.write(b"\n" + b"[hostile]\nvalue = " + b"x" * (70 * 1024))
            oversized = run_helper(root, "check")
            self.assertNotEqual(oversized.returncode, 0)
            self.assertIn("bounded input", oversized.stderr)

            subprocess.run(["/usr/bin/git", "-C", str(root), "init", "--quiet"], check=True)
            config_path.unlink()
            os.mkfifo(config_path)
            fifo = run_helper(root, "check")
            self.assertNotEqual(fifo.returncode, 0)
            self.assertIn("regular file", fifo.stderr)
        finally:
            subprocess.run(["rm", "-rf", str(root)], check=True)

    def test_hostile_git_and_remote_helper_cannot_capture_transport_environment(self) -> None:
        root = init_repo()
        try:
            marker = root / "captured"
            fake_git = root / "git"
            fake_remote = root / "git-remote-https"
            for executable in (fake_git, fake_remote):
                executable.write_text(
                    "#!/bin/sh\nprintf '%s\n' \"$" + "{HTTPS_PROXY:-}\" > '" + str(marker) + "'\nexit 99\n",
                    encoding="utf-8",
                )
                executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
            result = run_helper(
                root,
                "check",
                PATH=f"{root}:{os.environ['PATH']}",
                GIT_EXEC_PATH=str(root),
                HTTPS_PROXY="https://secret.invalid",
                GIT_ASKPASS=str(fake_git),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(marker.exists(), "hostile Git executable or remote helper ran")
        finally:
            subprocess.run(["rm", "-rf", str(root)], check=True)

    def test_local_read_and_ancestor_share_the_sanitized_boundary(self) -> None:
        root = init_repo()
        try:
            subprocess.run(["/usr/bin/git", "-C", str(root), "config", "user.email", "test@example.invalid"], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(root), "config", "user.name", "Test"], check=True)
            (root / "README").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["/usr/bin/git", "-C", str(root), "add", "README"], check=True)
            subprocess.run(["/usr/bin/git", "-C", str(root), "commit", "--quiet", "-m", "fixture"], check=True)
            commit = subprocess.check_output(["/usr/bin/git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            read = run_helper(root, "local-read", "rev-parse", "HEAD", GIT_DIR="/tmp/evil")
            self.assertEqual(read.returncode, 0, read.stderr)
            self.assertEqual(read.stdout.strip(), commit)
            ancestor = run_helper(root, "local-ancestor", commit, "HEAD")
            self.assertEqual(ancestor.returncode, 0, ancestor.stderr)
        finally:
            subprocess.run(["rm", "-rf", str(root)], check=True)


if __name__ == "__main__":
    unittest.main()
