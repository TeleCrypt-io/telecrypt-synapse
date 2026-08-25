#!/usr/bin/env python3
"""Prove that BuildKit resolved the inspected upstream base image digest."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


MAX_METADATA_BYTES = 1024 * 1024
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE_TAG_RE = re.compile(r"^(?P<image>[^@\s]+):(?P<tag>[^@\s]+)\Z")


def fail(message: str) -> None:
    raise SystemExit(f"base provenance: {message}")


def load_metadata(path: Path) -> dict[str, object]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        fail(f"could not read metadata: {exc}")
    if len(raw) > MAX_METADATA_BYTES:
        fail(f"metadata exceeds {MAX_METADATA_BYTES} bytes")
    try:
        metadata = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        fail(f"metadata is not valid JSON: {exc}")
    if not isinstance(metadata, dict):
        fail("metadata is not an object")
    return metadata


def load_provenance(metadata: dict[str, object]) -> dict[str, object]:
    warnings = metadata.get("buildx.build.warnings")
    if warnings is not None:
        if not isinstance(warnings, list):
            fail("BuildKit metadata warnings have an invalid shape")
        if warnings:
            fail("BuildKit reported provenance warnings")

    provenance = metadata.get("buildx.build.provenance")
    if isinstance(provenance, str):
        try:
            provenance = json.loads(provenance)
        except ValueError as exc:
            fail(f"BuildKit provenance is not valid JSON: {exc}")
    if not isinstance(provenance, dict):
        fail("BuildKit metadata has no provenance predicate")
    return provenance


def digest_matches(material: object, expected_digest: str) -> bool:
    if not isinstance(material, dict):
        return False
    digest = material.get("digest")
    return (
        isinstance(digest, dict)
        and digest.get("sha256") == expected_digest.removeprefix("sha256:")
    )


def validate_v02_materials(
    materials: object, image: str, tag: str, expected_digest: str
) -> None:
    if not isinstance(materials, list):
        fail("BuildKit v0.2 provenance has no materials list")
    expected_uris = {
        f"pkg:docker/{image}@{expected_digest}",
        f"pkg:docker/{image}@{tag}?platform=linux%2Famd64",
        f"docker-image://{image}@{expected_digest}",
    }
    base_materials = [
        material
        for material in materials
        if isinstance(material, dict)
        and isinstance(material.get("uri"), str)
        and material["uri"].startswith((f"pkg:docker/{image}@", f"docker-image://{image}@"))
    ]
    if len(base_materials) != 1:
        fail("BuildKit v0.2 provenance contains multiple or ambiguous base materials")
    material = base_materials[0]
    if material.get("uri") not in expected_uris or not digest_matches(material, expected_digest):
        fail("BuildKit v0.2 provenance does not bind the inspected base digest")


def validate_v1_dependencies(
    dependencies: object, image: str, tag: str, expected_digest: str
) -> None:
    if not isinstance(dependencies, list):
        fail("BuildKit v1 provenance has no resolved dependencies list")
    base_prefix = f"pkg:docker/{image}@"
    base_dependencies = [
        dependency
        for dependency in dependencies
        if isinstance(dependency, dict)
        and isinstance(dependency.get("uri"), str)
        and dependency["uri"].startswith(base_prefix)
    ]
    if len(base_dependencies) != 1:
        fail("BuildKit v1 provenance contains multiple or ambiguous base dependencies")
    expected_uri = f"{base_prefix}{tag}?platform=linux%2Famd64"
    dependency = base_dependencies[0]
    if dependency.get("uri") != expected_uri or not digest_matches(dependency, expected_digest):
        fail("BuildKit v1 provenance does not bind the inspected tagged amd64 base digest")


def validate_metadata(
    metadata: dict[str, object], base_ref: str, expected_digest: str
) -> None:
    if not DIGEST_RE.fullmatch(expected_digest):
        fail("expected base digest is not an exact SHA-256 digest")
    match = IMAGE_TAG_RE.fullmatch(base_ref)
    if not match:
        fail(f"base reference is not an exact tag reference: {base_ref}")
    image = match.group("image")
    tag = match.group("tag")

    provenance = load_provenance(metadata)
    if "buildDefinition" in provenance:
        build_definition = provenance.get("buildDefinition")
        if not isinstance(build_definition, dict):
            fail("BuildKit v1 provenance has an invalid buildDefinition")
        validate_v1_dependencies(
            build_definition.get("resolvedDependencies"), image, tag, expected_digest
        )
        return
    validate_v02_materials(provenance.get("materials"), image, tag, expected_digest)


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit("usage: verify_base_provenance.py METADATA BASE_REF DIGEST")
    validate_metadata(load_metadata(Path(sys.argv[1])), sys.argv[2], sys.argv[3])
    print("base provenance: inspected digest is the resolved build base")


if __name__ == "__main__":
    main()
