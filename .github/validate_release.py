#!/usr/bin/env python3
"""Validate the small immutable-release contract used by the publisher."""

from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path


DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")


def fail(message: str) -> None:
    raise SystemExit(f"release contract: {message}")


def validate_release(
    document: object,
    *,
    tag: str,
    asset_name: str,
    body: str,
    record_digest: str,
    record_size: int,
) -> None:
    if not isinstance(document, dict):
        fail("release response is not an object")
    release_id = document.get("id")
    if type(release_id) is not int or release_id <= 0:
        fail("release id is not a positive integer")
    if (
        document.get("tag_name") != tag
        or document.get("name") != tag
        or document.get("body") != body
        or document.get("draft") is not False
        or document.get("prerelease") is not False
        or document.get("immutable") is not True
    ):
        fail("release identity, body, or state differs from the exact contract")

    assets = document.get("assets")
    if not isinstance(assets, list) or len(assets) != 1 or not isinstance(assets[0], dict):
        fail("release must contain exactly one asset")
    asset = assets[0]
    asset_id = asset.get("id")
    if type(asset_id) is not int or asset_id <= 0:
        fail("asset id is not a positive integer")
    if (
        asset.get("name") != asset_name
        or asset.get("label") != ""
        or "label" not in asset
        or asset.get("state") != "uploaded"
        or type(asset.get("size")) is not int
        or asset["size"] != record_size
        or asset["size"] <= 0
        or asset.get("digest") != record_digest
        or not DIGEST_RE.fullmatch(str(asset.get("digest", "")))
    ):
        fail("asset identity, size, or digest differs from the exact contract")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("release", type=Path)
    args = parser.parse_args()
    required = {
        name: os.environ.get(name, "")
        for name in (
            "EXPECTED_TAG",
            "RELEASE_ASSET_NAME",
            "RELEASE_BODY",
            "RECORD_DIGEST",
            "RECORD_SIZE",
        )
    }
    if any(not value for value in required.values()):
        fail("release validation environment is incomplete")
    if not DIGEST_RE.fullmatch(required["RECORD_DIGEST"]):
        fail("expected record digest is not a SHA-256 digest")
    if not required["RECORD_SIZE"].isdigit() or int(required["RECORD_SIZE"]) <= 0:
        fail("expected record size is not positive")
    try:
        document = json.loads(args.release.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        fail(f"release JSON is unreadable: {exc}")
    validate_release(
        document,
        tag=required["EXPECTED_TAG"],
        asset_name=required["RELEASE_ASSET_NAME"],
        body=required["RELEASE_BODY"],
        record_digest=required["RECORD_DIGEST"],
        record_size=int(required["RECORD_SIZE"]),
    )


if __name__ == "__main__":
    main()
