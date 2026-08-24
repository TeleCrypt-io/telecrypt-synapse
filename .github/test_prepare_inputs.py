#!/usr/bin/env python3
"""Focused offline tests for the Controlplane release boundary."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path

import prepare_inputs
import validate_release
import verify_base_provenance


RELEASE = "0.4.0"
WHEEL = f"telecrypt_tier_controller-{RELEASE}-py3-none-any.whl"
DIGEST_ASSET = f"controlplane-{RELEASE}.digest.json"
WHEEL_SHA = "f" * 64
ANNOTATED_TAG_SHA = "a" * 40
SOURCE_COMMIT = "b" * 40


def record(**changes: object) -> bytes:
    payload: dict[str, object] = {
        "annotated_tag_sha": ANNOTATED_TAG_SHA,
        "digest": "sha256:" + "c" * 64,
        "image": prepare_inputs.CONTROLPLANE_IMAGE,
        "schema_version": 1,
        "source_commit": SOURCE_COMMIT,
        "tag": RELEASE,
    }
    payload.update(changes)
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def asset(name: str, asset_id: int, size: int, digest: str) -> dict[str, object]:
    return {
        "id": asset_id,
        "name": name,
        "size": size,
        "digest": f"sha256:{digest}",
        "label": None,
        "state": "uploaded",
        "created_at": "2026-08-22T00:00:01Z",
        "updated_at": "2026-08-22T00:00:02Z",
        "url": (
            f"https://api.github.com/repos/TeleCrypt-io/controlplane/releases/assets/{asset_id}"
        ),
        "browser_download_url": (
            f"https://github.com/TeleCrypt-io/controlplane/releases/download/{RELEASE}/{name}"
        ),
    }


def release_metadata() -> dict[str, object]:
    return {
        "id": 42,
        "tag_name": RELEASE,
        "name": RELEASE,
        "draft": False,
        "prerelease": False,
        "immutable": True,
        "body": f"Exact Controlplane release {RELEASE}.",
        "created_at": "2026-08-22T00:00:00Z",
        "published_at": "2026-08-23T00:00:00Z",
        "url": "https://api.github.com/repos/TeleCrypt-io/controlplane/releases/42",
        "assets_url": "https://api.github.com/repos/TeleCrypt-io/controlplane/releases/42/assets",
        "upload_url": "https://uploads.github.com/repos/TeleCrypt-io/controlplane/releases/42/assets{?name,label}",
        "html_url": f"https://github.com/TeleCrypt-io/controlplane/releases/tag/{RELEASE}",
        "tarball_url": f"https://api.github.com/repos/TeleCrypt-io/controlplane/tarball/{RELEASE}",
        "zipball_url": f"https://api.github.com/repos/TeleCrypt-io/controlplane/zipball/{RELEASE}",
        "assets": [
            asset(WHEEL, 1, 7, WHEEL_SHA),
            asset(DIGEST_ASSET, 2, 128, "0" * 64),
        ],
    }


class PrepareInputsTests(unittest.TestCase):
    def run_publish_release(
        self, record_bytes: bytes, **changes: str
    ) -> subprocess.CompletedProcess[str]:
        environment = {
            "GH_TOKEN": "offline-test-token",
            "GH_API_VERSION": "2026-03-10",
            "GITHUB_REPOSITORY": "TeleCrypt-io/telecrypt-synapse",
            "RELEASE_ASSET_NAME": f"telecrypt-synapse-{RELEASE}.digest.json",
            "EXPECTED_TAG": "1.159-tc3",
            "EXPECTED_SHA": SOURCE_COMMIT,
            "EXPECTED_ANNOTATED_TAG_SHA": ANNOTATED_TAG_SHA,
            "EXPECTED_DIGEST": "sha256:" + "c" * 64,
        }
        environment.update(changes)
        with tempfile.TemporaryDirectory() as directory:
            record_path = Path(directory) / "record.json"
            record_path.write_bytes(record_bytes)
            environment["RELEASE_RECORD"] = str(record_path)
            return subprocess.run(
                ["bash", ".github/publish_release.sh"],
                cwd=Path(__file__).resolve().parents[1],
                env={**os.environ, **environment},
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

    def test_exact_controlplane_record_and_rejects_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / DIGEST_ASSET
            path.write_bytes(record())
            prepare_inputs.validate_controlplane_digest(
                path, RELEASE, SOURCE_COMMIT, ANNOTATED_TAG_SHA
            )
            for invalid in (
                record(source_commit="d" * 40),
                record(annotated_tag_sha="e" * 40),
                record(extra="unexpected"),
                record(digest="f" * 64),
                record()[:-1] + b" ",
                b"x" * (prepare_inputs.MAX_DIGEST_JSON_BYTES + 1),
            ):
                path.write_bytes(invalid)
                with self.assertRaises(SystemExit):
                    prepare_inputs.validate_controlplane_digest(
                        path, RELEASE, SOURCE_COMMIT, ANNOTATED_TAG_SHA
                    )

    def test_exact_release_assets_and_wheel_metadata(self) -> None:
        metadata = release_metadata()
        wheel_asset, digest_asset = prepare_inputs.validate_controlplane_assets(
            metadata, RELEASE, WHEEL, WHEEL_SHA
        )
        self.assertEqual(wheel_asset["name"], WHEEL)
        self.assertEqual(digest_asset["name"], DIGEST_ASSET)
        metadata["assets"] = metadata["assets"] + [asset("extra", 3, 1, "1" * 64)]
        with self.assertRaises(SystemExit):
            prepare_inputs.validate_controlplane_assets(metadata, RELEASE, WHEEL, WHEEL_SHA)

    def test_exact_release_metadata_contract_and_rejects_drift(self) -> None:
        metadata = release_metadata()
        for html_url in (
            f"https://github.com/TeleCrypt-io/controlplane/releases/tag/{RELEASE}",
            f"https://github.com/TeleCrypt-io/controlplane/releases/{RELEASE}",
        ):
            prepare_inputs.validate_controlplane_release(
                {**metadata, "html_url": html_url}, RELEASE
            )
        for field, value in (
            ("name", "wrong-name"),
            ("id", 0),
            ("published_at", "2026-02-30T00:00:00Z"),
            ("published_at", "2026-08-23 00:00:00Z"),
            ("url", "https://api.github.com/other"),
            ("html_url", "https://github.com/TeleCrypt-io/controlplane/releases/tag/other"),
        ):
            invalid = dict(metadata)
            invalid[field] = value
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_controlplane_release(invalid, RELEASE)

    def test_asset_state_and_api_url_are_exact(self) -> None:
        for field, value in (
            ("state", "new"),
            (
                "url",
                "https://api.github.com/repos/TeleCrypt-io/controlplane/releases/assets/9",
            ),
            (
                "url",
                "https://user:password@api.github.com/repos/TeleCrypt-io/controlplane/releases/assets/1",
            ),
        ):
            metadata = release_metadata()
            metadata["assets"] = [dict(item) for item in metadata["assets"]]
            metadata["assets"][0][field] = value
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_controlplane_assets(metadata, RELEASE, WHEEL, WHEEL_SHA)

    def test_download_urls_reject_userinfo_and_redirect_userinfo(self) -> None:
        for url in (
            "https://user@example.com/archive.tar.gz",
            "https://:password@github.com/archive.tar.gz",
            "https://@github.com/archive.tar.gz",
            "https://user:@github.com/archive.tar.gz",
        ):
            with self.assertRaises(SystemExit):
                prepare_inputs.validate_download_url(url, "github.com")
            request = urllib.request.Request("https://github.com/start")
            with self.assertRaises(ValueError):
                prepare_inputs.SafeRedirectHandler().redirect_request(
                    request, None, 307, "temporary", {}, url
                )

    def test_publish_release_rejects_noncanonical_record_before_network(self) -> None:
        publish_record = record(tag="1.159-tc3")
        for invalid in (
            record(tag="1.159-tc3", extra="unexpected"),
            publish_record[:-1] + b" ",
        ):
            result = self.run_publish_release(invalid)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("recheck", result.stderr.lower())

    def test_publish_release_requires_reviewed_github_api_version(self) -> None:
        result = self.run_publish_release(
            record(tag="1.159-tc3"), GH_API_VERSION="2025-01-01"
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("2026-03-10", result.stderr)

    def test_strict_git_fetch_sanitizes_ambient_git_configuration(self) -> None:
        helper = (Path(__file__).parent / "strict_git_fetch.sh").read_text()
        self.assertIn("export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_GLOBAL=/dev/null", helper)
        self.assertIn("export GIT_CONFIG_COUNT=0 GIT_TERMINAL_PROMPT=0", helper)
        self.assertIn("GIT_CONFIG_PARAMETERS", helper)
        self.assertIn("GIT_ASKPASS SSH_ASKPASS GIT_SSH GIT_SSH_COMMAND GIT_PROXY_COMMAND", helper)
        self.assertIn("HTTP_PROXY HTTPS_PROXY ALL_PROXY", helper)
        self.assertIn("GIT_SSL_NO_VERIFY GIT_SSL_VERSION GIT_SSL_CIPHER_LIST", helper)
        self.assertIn("for config_scope in --local --worktree", helper)
        self.assertIn("url.*|http.*|credential.*|include*|core.ssh*|core.gitproxy", helper)
        self.assertIn("remote.*.uploadpack|remote.*.proxy", helper)
        self.assertIn("https://github.com/TeleCrypt-io/telecrypt-synapse.git", helper)
        self.assertIn("protocol.version=2", helper)
        self.assertIn("protocol.allow=never", helper)
        self.assertIn("protocol.https.allow=always", helper)
        self.assertIn("ulimit -f", helper)
        self.assertIn("timeout --signal=TERM --kill-after=5s", helper)
        self.assertNotIn('cat -- "$workdir/stdout"', helper)
        self.assertNotIn('cat -- "$workdir/stderr"', helper)

    def test_publish_release_binds_asset_to_record_bytes_not_image_digest(self) -> None:
        publish_release = (Path(__file__).parent / "publish_release.sh").read_text()
        self.assertIn('record_digest="sha256:', publish_release)
        self.assertIn('test "sha256:$(sha256sum -- "$destination"', publish_release)
        self.assertIn('test "$(wc -c <"$destination")" -eq "$record_size"', publish_release)
        self.assertIn("find_release_in_list", publish_release)
        self.assertIn("gh release upload", publish_release)
        self.assertIn("--clobber", publish_release)
        self.assertIn("Exact Synapse release for source commit", publish_release)
        self.assertNotIn("deliberately non-resumable", publish_release)
        self.assertIn('if (.draft | type) == "boolean" then (.draft | tostring)', publish_release)
        self.assertIn('--tag "$EXPECTED_TAG"', publish_release)
        self.assertIn('expected_release_url="https://github.com/$GITHUB_REPOSITORY/releases/tag/$EXPECTED_TAG"', publish_release)
        self.assertIn('test "$(wc -l <"$edit_stdout")" -eq 1', publish_release)
        self.assertIn('timeout --signal=TERM --kill-after=5s "${RELEASE_TIMEOUT_SECONDS}s"', publish_release)

    def test_synapse_release_contract_checks_asset_chronology_and_exact_links(self) -> None:
        digest = "sha256:" + "d" * 64
        document = {
            "id": 7,
            "tag_name": "1.159-tc3",
            "name": "1.159-tc3",
            "body": f"Exact Synapse release for source commit {SOURCE_COMMIT}.",
            "draft": False,
            "prerelease": False,
            "immutable": True,
            "created_at": "2026-08-22T00:00:00Z",
            "published_at": "2026-08-23T00:00:00Z",
            "url": "https://api.github.com/repos/TeleCrypt-io/telecrypt-synapse/releases/7",
            "assets_url": "https://api.github.com/repos/TeleCrypt-io/telecrypt-synapse/releases/7/assets",
            "upload_url": "https://uploads.github.com/repos/TeleCrypt-io/telecrypt-synapse/releases/7/assets{?name,label}",
            "html_url": "https://github.com/TeleCrypt-io/telecrypt-synapse/releases/tag/1.159-tc3",
            "tarball_url": "https://api.github.com/repos/TeleCrypt-io/telecrypt-synapse/tarball/1.159-tc3",
            "zipball_url": "https://api.github.com/repos/TeleCrypt-io/telecrypt-synapse/zipball/1.159-tc3",
            "assets": [{
                "name": "telecrypt-synapse-1.159-tc3.digest.json",
                "id": 8,
                "label": None,
                "state": "uploaded",
                "size": 10,
                "url": "https://api.github.com/repos/TeleCrypt-io/telecrypt-synapse/releases/assets/8",
                "browser_download_url": "https://github.com/TeleCrypt-io/telecrypt-synapse/releases/download/1.159-tc3/telecrypt-synapse-1.159-tc3.digest.json",
                "digest": digest,
                "created_at": "2026-08-22T00:00:01Z",
                "updated_at": "2026-08-22T00:00:02Z",
            }],
        }
        for html_url in (
            "https://github.com/TeleCrypt-io/telecrypt-synapse/releases/tag/1.159-tc3",
            "https://github.com/TeleCrypt-io/telecrypt-synapse/releases/1.159-tc3",
        ):
            candidate = dict(document, html_url=html_url)
            validate_release.validate_release(
                candidate,
                repository="TeleCrypt-io/telecrypt-synapse",
                tag="1.159-tc3",
                asset_name="telecrypt-synapse-1.159-tc3.digest.json",
                body=f"Exact Synapse release for source commit {SOURCE_COMMIT}.",
                record_digest=digest,
                record_size=10,
                draft=False,
                immutable=True,
            )
        invalid = dict(document)
        invalid["assets"] = [dict(document["assets"][0], updated_at="2026-08-21T00:00:02Z")]
        with self.assertRaises(SystemExit):
            validate_release.validate_release(
                invalid,
                repository="TeleCrypt-io/telecrypt-synapse",
                tag="1.159-tc3",
                asset_name="telecrypt-synapse-1.159-tc3.digest.json",
                body=f"Exact Synapse release for source commit {SOURCE_COMMIT}.",
                record_digest=digest,
                record_size=10,
                draft=False,
                immutable=True,
            )

    def test_draft_recovery_classifies_zero_exact_partial_and_ambiguous_assets(self) -> None:
        name = "telecrypt-synapse-1.159-tc3.digest.json"
        digest = "sha256:" + "d" * 64
        exact = {"name": name, "state": "uploaded", "size": 10, "digest": digest}
        self.assertEqual(validate_release.draft_asset_action({"assets": []}, name, digest, 10), "upload")
        self.assertEqual(validate_release.draft_asset_action({"assets": [exact]}, name, digest, 10), "verify")
        self.assertEqual(validate_release.draft_asset_action({"assets": [{**exact, "size": 3}]}, name, digest, 10), "replace")
        with self.assertRaises(SystemExit):
            validate_release.draft_asset_action({"assets": [exact, exact]}, name, digest, 10)
        with self.assertRaises(SystemExit):
            validate_release.draft_asset_action({"assets": [{**exact, "name": "wrong"}]}, name, digest, 10)

    def test_image_archive_handoff_binds_test_output_to_publish_input(self) -> None:
        workflow = (Path(__file__).parent / "workflows" / "image.yml").read_text()
        self.assertIn("archive_sha256: ${{ steps.archive.outputs.sha256 }}", workflow)
        self.assertIn("archive_size: ${{ steps.archive.outputs.size }}", workflow)
        self.assertIn("EXPECTED_ARCHIVE_SHA256: ${{ needs.test.outputs.archive_sha256 }}", workflow)
        self.assertIn("EXPECTED_ARCHIVE_SIZE: ${{ needs.test.outputs.archive_size }}", workflow)
        self.assertIn('test "$actual_archive_sha256" = "$EXPECTED_ARCHIVE_SHA256"', workflow)
        self.assertIn('test "$actual_archive_size" = "$EXPECTED_ARCHIVE_SIZE"', workflow)
        self.assertIn(
            "PYTHONDONTWRITEBYTECODE=1 python3 .github/test_prepare_inputs.py", workflow
        )
        self.assertIn(
            "PYTHONDONTWRITEBYTECODE=1 python3 .github/test_verify_registry_image.py", workflow
        )
        self.assertIn(
            "PYTHONDONTWRITEBYTECODE=1 python3 .github/test_strict_git_fetch.py", workflow
        )

    def test_base_materialization_is_explicitly_linux_amd64_and_dockerfile_uses_tag(self) -> None:
        workflow = (Path(__file__).parent / "workflows" / "image.yml").read_text()
        dockerfile = (Path(__file__).parents[1] / "Dockerfile").read_text()
        self.assertIn('docker pull --quiet --platform linux/amd64 "$BASE_REF@$base_digest"', workflow)
        self.assertIn("FROM ghcr.io/element-hq/synapse:v${SYNAPSE_VERSION}", dockerfile)
        self.assertNotIn("FROM ghcr.io/element-hq/synapse@${SYNAPSE_BASE_DIGEST}", dockerfile)

    def test_publish_release_bounds_and_rejects_command_diagnostics(self) -> None:
        publish_release = (Path(__file__).parent / "publish_release.sh").read_text()
        self.assertIn('2>"$stderr_file"', publish_release)
        self.assertIn("GitHub API emitted unexpected diagnostics", publish_release)
        self.assertIn('"$workdir/create.stderr"', publish_release)
        self.assertIn("GitHub draft Release was not recoverable after create", publish_release)
        self.assertIn("MAX_RELEASE_PAGES=10", publish_release)
        self.assertIn('expected_404_error="gh: HTTP 404: Not Found (https://api.github.com/repos/$GITHUB_REPOSITORY/releases/tags/$EXPECTED_TAG)"', publish_release)

    def test_annotated_tag_is_peeled_to_commit(self) -> None:
        responses = {
            f"git/ref/tags/{RELEASE}": {
                "ref": f"refs/tags/{RELEASE}",
                "url": f"https://api.github.com/repos/TeleCrypt-io/controlplane/git/refs/tags/{RELEASE}",
                "object": {
                    "type": "tag",
                    "sha": ANNOTATED_TAG_SHA,
                    "url": f"https://api.github.com/repos/TeleCrypt-io/controlplane/git/tags/{ANNOTATED_TAG_SHA}",
                },
            },
            f"git/tags/{ANNOTATED_TAG_SHA}": {
                "type": "tag",
                "sha": ANNOTATED_TAG_SHA,
                "tag": RELEASE,
                "url": f"https://api.github.com/repos/TeleCrypt-io/controlplane/git/tags/{ANNOTATED_TAG_SHA}",
                "object": {
                    "type": "commit",
                    "sha": SOURCE_COMMIT,
                    "url": f"https://api.github.com/repos/TeleCrypt-io/controlplane/git/commits/{SOURCE_COMMIT}",
                },
            },
        }
        original = prepare_inputs.fetch_controlplane_api
        prepare_inputs.fetch_controlplane_api = lambda endpoint, max_bytes: responses[endpoint]
        try:
            self.assertEqual(
                prepare_inputs.fetch_controlplane_annotated_tag(RELEASE),
                (ANNOTATED_TAG_SHA, SOURCE_COMMIT),
            )
            responses[f"git/ref/tags/{RELEASE}"]["object"]["type"] = "commit"
            with self.assertRaises(SystemExit):
                prepare_inputs.fetch_controlplane_annotated_tag(RELEASE)
        finally:
            prepare_inputs.fetch_controlplane_api = original

    def test_build_provenance_binds_the_inspected_base_digest(self) -> None:
        digest = "sha256:" + "1" * 64
        metadata = {
            "buildx.build.provenance": {
                "materials": [
                    {
                        "uri": f"pkg:docker/ghcr.io/element-hq/synapse@{digest}",
                        "digest": {"sha256": "1" * 64},
                    }
                ]
            }
        }
        verify_base_provenance.validate_metadata(
            metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
        )
        invalid_metadata = dict(metadata)
        invalid_metadata["buildx.build.provenance"] = {"materials": []}
        with self.assertRaises(SystemExit):
            verify_base_provenance.validate_metadata(
                invalid_metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
            )

    def test_build_provenance_accepts_v1_tagged_amd64_dependency(self) -> None:
        digest = "sha256:" + "2" * 64
        metadata = {
            "buildx.build.provenance": {
                "buildDefinition": {
                    "resolvedDependencies": [
                        {
                            "uri": "pkg:docker/ghcr.io/element-hq/synapse@v1.159.0?platform=linux%2Famd64",
                            "digest": {"sha256": "2" * 64},
                        }
                    ]
                }
            },
            "buildx.build.warnings": [],
        }
        verify_base_provenance.validate_metadata(
            metadata, "ghcr.io/element-hq/synapse:v1.159.0", digest
        )
        for invalid in (
            {
                **metadata,
                "buildx.build.provenance": {
                    "buildDefinition": {
                        "resolvedDependencies": [
                            {
                                "uri": "pkg:docker/ghcr.io/element-hq/synapse@v1.159.0?platform=linux%2Farm64",
                                "digest": {"sha256": "2" * 64},
                            }
                        ]
                    }
                },
            },
            {**metadata, "buildx.build.warnings": [{"message": "warning"}]},
        ):
            with self.assertRaises(SystemExit):
                verify_base_provenance.validate_metadata(
                    invalid, "ghcr.io/element-hq/synapse:v1.159.0", digest
                )


if __name__ == "__main__":
    unittest.main()
