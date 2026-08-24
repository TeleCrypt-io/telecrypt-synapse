#!/usr/bin/env python3
"""Validate the reviewed dependency lock and its exact binary wheelhouse."""

from __future__ import annotations

import hashlib
import re
import sys
import zipfile
from pathlib import Path


EXPECTED_VERSIONS = {
    "boto3": "1.43.78",
    "botocore": "1.43.78",
    "humanize": "4.16.0",
    "jmespath": "1.1.0",
    "s3transfer": "0.19.2",
    "tqdm": "4.70.0",
}
LINE_RE = re.compile(r"^(?P<name>[A-Za-z0-9_.-]+)==(?P<version>[^ ]+) --hash=sha256:(?P<hash>[0-9a-f]{64})$")
HASH_CHUNK_BYTES = 1024 * 1024
MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_WHEELHOUSE_BYTES = 128 * 1024 * 1024
MAX_WHEELHOUSE_FILES = 16


def fail(message: str) -> None:
    raise SystemExit(f"dependency lock: {message}")


def load_lock(path: Path) -> dict[str, tuple[str, str]]:
    found: dict[str, tuple[str, str]] = {}
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line or line.startswith("#"):
            continue
        match = LINE_RE.fullmatch(line)
        if match is None:
            fail(f"line {line_number} is not an exact hashed requirement")
        name = match.group("name").lower().replace("_", "-")
        if name in found:
            fail(f"line {line_number} duplicates {name}")
        found[name] = (match.group("version"), match.group("hash"))
    if set(found) != set(EXPECTED_VERSIONS):
        fail(f"contents differ from reviewed distribution set: {sorted(found)!r}")
    actual_versions = {name: version for name, (version, _) in found.items()}
    if actual_versions != EXPECTED_VERSIONS:
        fail(f"versions differ from reviewed set: {actual_versions!r}")
    return found


def wheel_filename(name: str, version: str) -> str:
    return f"{name.replace('-', '_')}-{version}-py3-none-any.whl"


def sha256_file(path: Path, max_bytes: int = MAX_FILE_BYTES) -> str:
    digest = hashlib.sha256()
    total = 0
    with path.open("rb") as stream:
        while chunk := stream.read(HASH_CHUNK_BYTES):
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"{path.name} exceeds the {max_bytes} byte file limit")
            digest.update(chunk)
    return digest.hexdigest()


def validate_wheelhouse(path: Path, expected: dict[str, tuple[str, str]]) -> None:
    if not path.is_dir():
        fail(f"wheelhouse is missing: {path}")
    entries = []
    total_bytes = 0
    for item in path.iterdir():
        if len(entries) >= MAX_WHEELHOUSE_FILES:
            fail(f"wheelhouse has more than {MAX_WHEELHOUSE_FILES} entries")
        entries.append(item)
        if item.is_file() and not item.is_symlink():
            try:
                total_bytes += item.stat().st_size
            except OSError as exc:
                fail(f"could not stat {item.name}: {exc}")
            if total_bytes > MAX_WHEELHOUSE_BYTES:
                fail(f"wheelhouse exceeds the {MAX_WHEELHOUSE_BYTES} byte total limit")
    entries.sort()
    if any(not item.is_file() or item.is_symlink() for item in entries):
        fail("wheelhouse must contain only regular files")
    files = entries
    expected_names = {wheel_filename(name, version) for name, (version, _) in expected.items()}
    actual_names = {item.name for item in files}
    if actual_names != expected_names:
        fail(f"wheelhouse files differ from reviewed binary set: {sorted(actual_names)!r}")

    for name, (version, digest) in expected.items():
        wheel_path = path / wheel_filename(name, version)
        if wheel_path.suffix != ".whl":
            fail(f"{wheel_path.name} is not a wheel")
        try:
            actual_digest = sha256_file(wheel_path)
        except (OSError, ValueError) as exc:
            fail(f"could not hash {wheel_path.name}: {exc}")
        if actual_digest != digest:
            fail(f"{wheel_path.name} has digest {actual_digest}, expected {digest}")
        try:
            with zipfile.ZipFile(wheel_path) as wheel:
                metadata_name = next(
                    item for item in wheel.namelist() if item.endswith(".dist-info/METADATA")
                )
                metadata = wheel.read(metadata_name).decode("utf-8")
        except (OSError, StopIteration, UnicodeDecodeError, zipfile.BadZipFile) as exc:
            fail(f"{wheel_path.name} is not a readable wheel: {exc}")
        metadata_values = dict(
            line.split(": ", 1)
            for line in metadata.splitlines()
            if ": " in line and line.split(": ", 1)[0] in {"Name", "Version"}
        )
        if metadata_values.get("Name", "").lower().replace("_", "-") != name:
            fail(f"{wheel_path.name} has unexpected package metadata")
        if metadata_values.get("Version") != version:
            fail(f"{wheel_path.name} has unexpected package version")


def main() -> None:
    if len(sys.argv) not in {2, 3}:
        raise SystemExit("usage: validate_dependencies.py s3-provider.lock [wheelhouse]")
    expected = load_lock(Path(sys.argv[1]))
    if len(sys.argv) == 3:
        validate_wheelhouse(Path(sys.argv[2]), expected)
    print("dependency lock: ok")


if __name__ == "__main__":
    main()
