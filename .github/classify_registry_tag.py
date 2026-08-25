#!/usr/bin/env python3
"""Classify one GHCR tag from an authenticated GitHub Packages response."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import NoReturn


MAX_RESPONSE_BYTES = 1024 * 1024
MAX_PAGES = 100
MAX_VERSIONS = 10_000
MAX_TEXT_BYTES = 256
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def fail(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    raise SystemExit(1)


def _object(value: object, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        fail(f"{context} has an invalid shape")
    return value


def _string(value: object, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value.encode("utf-8")) > MAX_TEXT_BYTES
        or any(ord(char) < 0x20 for char in value)
    ):
        fail(f"{context} is incomplete")
    return value


def classify(document: object, tag: str) -> str:
    """Return ``absent`` or ``existing <digest>`` for one exact tag."""

    tag = _string(tag, "requested tag")
    pages = document if isinstance(document, list) else None
    if pages is None or len(pages) > MAX_PAGES:
        fail("package versions response has an invalid page shape")

    versions: list[dict[str, object]] = []
    seen_ids: set[int] = set()
    seen_tags: set[str] = set()
    for page_number, page_value in enumerate(pages, start=1):
        page = page_value if isinstance(page_value, list) else None
        if page is None:
            fail(f"package versions page {page_number} has an invalid shape")
        versions.extend(_object(version, f"package version {page_number}") for version in page)
        if len(versions) > MAX_VERSIONS:
            fail("package versions response is too large")

    for version_number, version in enumerate(versions, start=1):
        version_id = version.get("id")
        if not isinstance(version_id, int) or isinstance(version_id, bool) or version_id <= 0:
            fail(f"package version {version_number} has an invalid id")
        if version_id in seen_ids:
            fail("package versions response contains duplicate version ids")
        seen_ids.add(version_id)

        digest = _string(version.get("name"), f"package version {version_number} digest")
        if not DIGEST_RE.fullmatch(digest):
            fail(f"package version {version_number} has an invalid digest")

        metadata = _object(version.get("metadata"), f"package version {version_number} metadata")
        if metadata.get("package_type") != "container":
            fail(f"package version {version_number} is not a container")
        container = _object(
            metadata.get("container"), f"package version {version_number} container metadata"
        )
        tags = container.get("tags")
        if not isinstance(tags, list):
            fail(f"package version {version_number} tags are incomplete")
        for version_tag in tags:
            version_tag = _string(version_tag, f"package version {version_number} tag")
            if version_tag in seen_tags:
                fail("package versions response contains duplicate tags")
            seen_tags.add(version_tag)

        if "digest" in container:
            metadata_digest = container["digest"]
            metadata_digest = _string(
                metadata_digest, f"package version {version_number} metadata digest"
            )
            if not DIGEST_RE.fullmatch(metadata_digest) or metadata_digest != digest:
                fail(f"package version {version_number} has mismatched digest metadata")

        versions[version_number - 1] = version | {"_digest": digest, "_tags": tags}

    matches = [version for version in versions if tag in version["_tags"]]
    if not matches:
        return "absent"
    if len(matches) != 1:
        fail("package versions response contains duplicate requested tags")
    return f"existing {matches[0]['_digest']}"


def parse_response(path: Path, tag: str) -> str:
    try:
        response = path.read_bytes()
    except OSError as error:
        fail(f"could not read package versions response: {error.strerror or 'I/O error'}")
    if len(response) > MAX_RESPONSE_BYTES:
        fail("package versions response exceeds the bounded size")
    try:
        document = json.loads(response)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("package versions response is not valid JSON")
    return classify(document, tag)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("response", type=Path)
    parser.add_argument("tag")
    args = parser.parse_args()
    print(parse_response(args.response, args.tag))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
