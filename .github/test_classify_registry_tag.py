#!/usr/bin/env python3
"""Hostile offline tests for the authenticated package-tag classifier."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "classify_registry_tag.py"
WORKFLOW = ROOT / ".github" / "workflows" / "image.yml"
TAG = "1.159-tc3"
DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64


def version(
    version_id: int = 1,
    version_digest: str = DIGEST,
    tags: list[str] | None = None,
    **container_changes: object,
) -> dict[str, object]:
    container: dict[str, object] = {"tags": [] if tags is None else tags}
    container.update(container_changes)
    return {
        "id": version_id,
        "name": version_digest,
        "metadata": {"package_type": "container", "container": container},
    }


class ClassifyRegistryTagTests(unittest.TestCase):
    def run_classifier(
        self, document: object, *, tag: str = TAG, raw: bytes | None = None
    ) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            response = Path(directory) / "versions.json"
            response.write_bytes(raw if raw is not None else json.dumps(document).encode())
            return subprocess.run(
                ["python3", str(SCRIPT), str(response), tag],
                cwd=ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

    def test_classifies_exact_tag_across_pages_and_uses_version_digest(self) -> None:
        result = self.run_classifier(
            [[version(tags=["other"], digest=DIGEST)], [version(2, OTHER_DIGEST, [TAG])]]
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"existing {OTHER_DIGEST}\n")

    def test_classifies_valid_empty_package_as_absent(self) -> None:
        result = self.run_classifier([[]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "absent\n")

    def test_requires_exact_tag_match(self) -> None:
        result = self.run_classifier([[version(tags=[f"{TAG}-suffix"])]])
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "absent\n")

    def test_rejects_malformed_incomplete_and_duplicate_state(self) -> None:
        invalid_documents: list[object] = [
            {"message": "manifest unknown"},
            [[version(tags=[TAG], version_digest="sha256:not-a-digest")]],
            [[version(tags=[TAG])], {"not": "a page"}],
            [[{**version(tags=[TAG]), "metadata": {"package_type": "container"}}]],
            [[version(tags=["other", "other"])]],
            [[version(tags=[TAG]), version(2, OTHER_DIGEST, [TAG])]],
            [[version(tags=[TAG]), version(1, OTHER_DIGEST, ["other"])]],
        ]
        for document in invalid_documents:
            with self.subTest(document=document):
                result = self.run_classifier(document)
                self.assertNotEqual(result.returncode, 0)

    def test_rejects_null_or_mismatched_metadata_digest(self) -> None:
        for digest in (None, OTHER_DIGEST, "sha256:not-a-digest"):
            with self.subTest(digest=digest):
                result = self.run_classifier(
                    [[version(tags=[TAG], **{"digest": digest})]]
                )
                self.assertNotEqual(result.returncode, 0)

    def test_rejects_invalid_response_encoding_and_size(self) -> None:
        invalid_encoding = self.run_classifier([], raw=b"\xff")
        self.assertNotEqual(invalid_encoding.returncode, 0)
        oversized = self.run_classifier([], raw=b" " * (1024 * 1024 + 1))
        self.assertNotEqual(oversized.returncode, 0)
        oversized_tag = self.run_classifier([[]], tag="x" * 257)
        self.assertNotEqual(oversized_tag.returncode, 0)

    def test_workflow_uses_authenticated_bounded_api_for_both_classifiers(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertNotIn("grep -Eqi 'manifest unknown|no such manifest'", workflow)
        self.assertEqual(
            workflow.count('classification="$(python3 .github/classify_registry_tag.py "$package_versions_file" "$TAG")"'),
            2,
        )
        self.assertEqual(workflow.count("--paginate --slurp"), 2)
        self.assertEqual(workflow.count("X-GitHub-Api-Version: 2026-03-10"), 2)
        self.assertEqual(
            workflow.count("GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}\n          TAG:"), 2
        )
        self.assertIn("packages: write", workflow)
        self.assertIn("orgs/TeleCrypt-io/packages/container/telecrypt-synapse/versions?per_page=100", workflow)
        self.assertNotIn("docker manifest inspect \"$IMAGE:$TAG\"", workflow)

    def test_every_bounded_docker_helper_is_scoped_to_its_step_temp_area(self) -> None:
        workflow = WORKFLOW.read_text(encoding="utf-8")
        steps = workflow.split("      - ")[1:]
        bounded_steps = [step for step in steps if ".github/run_bounded_command.sh" in step]
        self.assertEqual(workflow.count(".github/run_bounded_command.sh"), 7)
        self.assertGreaterEqual(len(bounded_steps), 5)
        for step in bounded_steps:
            with self.subTest(step=step.splitlines()[0]):
                self.assertIn("run: |", step)
                self.assertIn("set -euo pipefail", step)
                self.assertIn("$RUNNER_TEMP", step)


if __name__ == "__main__":
    unittest.main()
