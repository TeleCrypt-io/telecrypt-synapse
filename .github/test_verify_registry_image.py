#!/usr/bin/env python3
"""Offline tests for the registry image identity verifier."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "verify_registry_image.sh"
IMAGE_REF = "ghcr.io/telecrypt-io/telecrypt-synapse:1.159-tc3"
IMAGE_NAME = IMAGE_REF.rsplit(":", 1)[0]
IMAGE_ID = "sha256:" + "1" * 64
MANIFEST_DIGEST = "sha256:" + "2" * 64
OTHER_MANIFEST_DIGEST = "sha256:" + "3" * 64


def manifest(digest: str, *, platform: bool = False, os_name: str = "linux", architecture: str = "amd64") -> dict[str, object]:
    descriptor: dict[str, object] = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "digest": digest,
    }
    if platform:
        descriptor["platform"] = {"os": os_name, "architecture": architecture}
    return {
        "Descriptor": descriptor,
        "OCIManifest": {
            "config": {"digest": IMAGE_ID},
            "layers": [{"digest": "sha256:" + "4" * 64, "size": 1}],
        },
    }


def fake_docker(path: Path) -> None:
    path.write_text(
        """#!/bin/sh
set -eu
case "$1 ${2-}" in
  "manifest inspect")
    count=$(cat "$FAKE_DOCKER_COUNTER")
    printf '%s\n' "$((count + 1))" >"$FAKE_DOCKER_COUNTER"
    if [ "$count" -eq 0 ]; then
      cat "$FAKE_DOCKER_FIRST"
    else
      cat "$FAKE_DOCKER_SECOND"
    fi
    ;;
  "pull --platform")
    ;;
  "image inspect")
    for argument in "$@"; do
      case "$argument" in
        *'{{.Id}}'*) printf '%s\n' "$FAKE_DOCKER_IMAGE_ID"; exit 0 ;;
        *'{{.Os}}/{{.Architecture}}'*) printf '%s\n' "$FAKE_DOCKER_PLATFORM"; exit 0 ;;
        *'{{range .RepoDigests}}'*) printf '%s@%s\n' "$FAKE_DOCKER_IMAGE_NAME" "$FAKE_DOCKER_MANIFEST_DIGEST"; exit 0 ;;
      esac
    done
    ;;
esac
""",
        encoding="utf-8",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class VerifyRegistryImageTests(unittest.TestCase):
    def run_verifier(
        self,
        first: object,
        second: object | None = None,
        *,
        platform: str = "linux/amd64",
        expected_digest: str = MANIFEST_DIGEST,
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fake_bin = root / "docker"
            fake_docker(fake_bin)
            first_path = root / "first.json"
            second_path = root / "second.json"
            counter_path = root / "counter"
            first_path.write_text(json.dumps(first), encoding="utf-8")
            second_path.write_text(json.dumps(second if second is not None else first), encoding="utf-8")
            counter_path.write_text("0", encoding="ascii")
            environment = {
                **os.environ,
                "PATH": f"{root}:{os.environ['PATH']}",
                "FAKE_DOCKER_COUNTER": str(counter_path),
                "FAKE_DOCKER_FIRST": str(first_path),
                "FAKE_DOCKER_SECOND": str(second_path),
                "FAKE_DOCKER_IMAGE_ID": IMAGE_ID,
                "FAKE_DOCKER_PLATFORM": platform,
                "FAKE_DOCKER_IMAGE_NAME": IMAGE_NAME,
                "FAKE_DOCKER_MANIFEST_DIGEST": expected_digest,
            }
            return subprocess.run(
                ["bash", str(SCRIPT), IMAGE_REF, IMAGE_ID, expected_digest],
                env=environment,
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )

    def test_accepts_direct_single_manifest_without_descriptor_platform(self) -> None:
        result = self.run_verifier(manifest(MANIFEST_DIGEST))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"digest={MANIFEST_DIGEST}", result.stdout)

    def test_accepts_one_platform_index_response(self) -> None:
        result = self.run_verifier([manifest(MANIFEST_DIGEST, platform=True)])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rejects_wrong_platform_even_for_direct_manifest(self) -> None:
        result = self.run_verifier(manifest(MANIFEST_DIGEST), platform="linux/arm64")
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_wrong_platform_in_one_platform_index(self) -> None:
        result = self.run_verifier([manifest(MANIFEST_DIGEST, platform=True, architecture="arm64")])
        self.assertNotEqual(result.returncode, 0)

    def test_rejects_manifest_digest_change_after_pull(self) -> None:
        result = self.run_verifier(manifest(MANIFEST_DIGEST), manifest(OTHER_MANIFEST_DIGEST))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("changed while it was being pulled", result.stderr)


if __name__ == "__main__":
    unittest.main()
