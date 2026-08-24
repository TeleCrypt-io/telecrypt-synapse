#!/usr/bin/env python3
"""Write the deterministic immutable-release image digest record."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


HEX_SHA_RE = re.compile(r"[0-9a-f]{40}\Z")
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
TAG_RE = re.compile(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)-tc[1-9][0-9]*\Z")
IMAGE = "ghcr.io/telecrypt-io/telecrypt-synapse"


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"release record: {name} is required")
    return value


def main() -> None:
    output = Path(required("RECORD_PATH"))
    image = required("RECORD_IMAGE")
    tag = required("RECORD_TAG")
    digest = required("RECORD_DIGEST")
    source_sha = required("RECORD_SOURCE_SHA")
    annotated_tag_sha = required("RECORD_ANNOTATED_TAG_SHA")
    if image != IMAGE:
        raise SystemExit(f"release record: unexpected image {image!r}")
    if not TAG_RE.fullmatch(tag):
        raise SystemExit(f"release record: unexpected image tag {tag!r}")
    if not DIGEST_RE.fullmatch(digest):
        raise SystemExit(f"release record: unexpected image digest {digest!r}")
    for name, value in (("source commit", source_sha), ("annotated tag", annotated_tag_sha)):
        if not HEX_SHA_RE.fullmatch(value):
            raise SystemExit(f"release record: {name} is not a Git SHA")

    record = {
        "annotated_tag_sha": annotated_tag_sha,
        "digest": digest,
        "image": image,
        "schema_version": 1,
        "source_commit": source_sha,
        "tag": tag,
    }
    output.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
