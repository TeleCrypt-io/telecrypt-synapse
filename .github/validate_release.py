#!/usr/bin/env python3
"""Validate the exact GitHub Release evidence used by the image publisher."""

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
from pathlib import Path


RFC3339_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]+)?Z\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def fail(message: str) -> None:
    raise SystemExit(f"release contract: {message}")


def timestamp(value: object, label: str) -> datetime.datetime:
    if not isinstance(value, str) or not RFC3339_RE.fullmatch(value):
        fail(f"{label} is not a UTC RFC3339 timestamp")
    try:
        parsed = datetime.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        fail(f"{label} is not a real UTC timestamp")
    if parsed.tzinfo != datetime.timezone.utc:
        fail(f"{label} is not UTC")
    return parsed


def validate_release(
    document: object,
    *,
    repository: str,
    tag: str,
    asset_name: str,
    body: str,
    record_digest: str,
    record_size: int,
    draft: bool,
    immutable: bool,
) -> None:
    if not isinstance(document, dict):
        fail("release response is not an object")
    release_id = document.get("id")
    if type(release_id) is not int or release_id <= 0:
        fail("release id is not a positive integer")
    api_root = f"https://api.github.com/repos/{repository}"
    expected_html_urls = {
        f"https://github.com/{repository}/releases/{tag}",
        f"https://github.com/{repository}/releases/tag/{tag}",
    }
    if (
        document.get("tag_name") != tag
        or document.get("name") != tag
        or document.get("body") != body
        or document.get("draft") is not draft
        or document.get("prerelease") is not False
        or document.get("immutable") is not immutable
        or document.get("url") != f"{api_root}/releases/{release_id}"
        or document.get("assets_url") != f"{api_root}/releases/{release_id}/assets"
        or document.get("upload_url") != f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets{{?name,label}}"
        or document.get("html_url") not in expected_html_urls
        or document.get("tarball_url") != f"https://api.github.com/repos/{repository}/tarball/{tag}"
        or document.get("zipball_url") != f"https://api.github.com/repos/{repository}/zipball/{tag}"
    ):
        fail("release identity, links, body, or state differs from the exact contract")

    created = timestamp(document.get("created_at"), "release.created_at")
    published_value = document.get("published_at")
    if draft:
        if published_value is not None:
            fail("draft release has a publication timestamp")
        published = None
    else:
        published = timestamp(published_value, "release.published_at")
        if created > published:
            fail("release.created_at is after release.published_at")

    assets = document.get("assets")
    if not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
        fail("release must contain exactly one asset")
    asset = assets[0]
    asset_id = asset.get("id")
    if type(asset_id) is not int or asset_id <= 0:
        fail("asset id is not a positive integer")
    if (
        asset.get("name") != asset_name
        or asset.get("label") is not None
        or "label" not in asset
        or asset.get("state") != "uploaded"
        or asset.get("url") != f"{api_root}/releases/assets/{asset_id}"
        or asset.get("browser_download_url") != f"https://github.com/{repository}/releases/download/{tag}/{asset_name}"
        or type(asset.get("size")) is not int
        or asset["size"] != record_size
        or asset["size"] <= 0
        or asset.get("digest") != record_digest
        or not DIGEST_RE.fullmatch(str(asset.get("digest", "")))
    ):
        fail("asset identity, links, size, or digest differs from the exact contract")
    asset_created = timestamp(asset.get("created_at"), "asset.created_at")
    asset_updated = timestamp(asset.get("updated_at"), "asset.updated_at")
    if not created <= asset_created <= asset_updated:
        fail("release and asset timestamps are out of order")
    if published is not None and asset_updated > published:
        fail("asset.updated_at is after release.published_at")


def draft_asset_action(document: object, asset_name: str, record_digest: str, record_size: int) -> str:
    """Classify a draft asset without guessing about ambiguous state."""
    if not isinstance(document, dict) or not isinstance(document.get("assets"), list):
        fail("draft asset response is malformed")
    assets = document["assets"]
    if not assets:
        return "upload"
    if len(assets) != 1 or not isinstance(assets[0], dict):
        fail("draft asset set is ambiguous")
    asset = assets[0]
    if asset.get("name") != asset_name:
        fail("draft contains an unexpected asset")
    if asset.get("state") == "uploaded" and asset.get("size") == record_size and asset.get("digest") == record_digest:
        return "verify"
    return "replace"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    parser.add_argument("--draft", action="store_true")
    args = parser.parse_args()
    required = {
        name: os.environ.get(name, "")
        for name in (
            "GITHUB_REPOSITORY",
            "EXPECTED_TAG",
            "RELEASE_ASSET_NAME",
            "RELEASE_BODY",
            "RECORD_DIGEST",
            "RECORD_SIZE",
            "EXPECTED_IMMUTABLE",
        )
    }
    if any(not value for value in required.values()):
        fail("release validation environment is incomplete")
    if not DIGEST_RE.fullmatch(required["RECORD_DIGEST"]):
        fail("expected record digest is not a SHA-256 digest")
    if not required["RECORD_SIZE"].isdigit() or int(required["RECORD_SIZE"]) <= 0:
        fail("expected record size is not positive")
    expected_immutable = required["EXPECTED_IMMUTABLE"] == "true"
    if required["EXPECTED_IMMUTABLE"] not in {"true", "false"}:
        fail("expected immutable state is invalid")
    try:
        document = json.loads(args.release.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail(f"release JSON is unreadable: {exc}")
    validate_release(
        document,
        repository=required["GITHUB_REPOSITORY"],
        tag=required["EXPECTED_TAG"],
        asset_name=required["RELEASE_ASSET_NAME"],
        body=required["RELEASE_BODY"],
        record_digest=required["RECORD_DIGEST"],
        record_size=int(required["RECORD_SIZE"]),
        draft=args.draft,
        immutable=expected_immutable,
    )


if __name__ == "__main__":
    main()
