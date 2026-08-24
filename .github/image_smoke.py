#!/usr/bin/env python3
"""Validate the installed Synapse image contract from inside the image."""

from __future__ import annotations

import importlib.metadata as metadata
import os
from pathlib import Path

from synapse.module_api import ModuleApi
import s3_storage_provider
import tier_controller
from tier_controller import TierController


def require(condition: bool, message: object) -> None:
    if not condition:
        raise SystemExit(f"image smoke: {message}")


def expected(name: str) -> str:
    value = os.environ.get(name)
    require(bool(value), f"missing {name}")
    return value


expected_versions = {
    "matrix-synapse": expected("EXPECTED_SYNAPSE_VERSION"),
    "synapse-s3-storage-provider": expected("EXPECTED_S3_PROVIDER_VERSION"),
    "telecrypt-tier-controller": expected("EXPECTED_CONTROLPLANE_RELEASE"),
}
actual_versions = {name: metadata.version(name) for name in expected_versions}
require(actual_versions == expected_versions, (expected_versions, actual_versions))

installed_modules = (tier_controller, s3_storage_provider)
require(
    all("site-packages" in Path(module.__file__).parts for module in installed_modules),
    tuple(module.__file__ for module in installed_modules),
)

required_files = (Path("/licenses/LICENSE"), Path("/licenses/THIRD_PARTY_NOTICES.md"))
require(
    all(path.is_file() and path.stat().st_size > 0 for path in required_files), required_files
)

controller_files = {
    str(path) for path in (metadata.distribution("telecrypt-tier-controller").files or ())
}
require(
    any(path.endswith(".dist-info/licenses/LICENSE") for path in controller_files),
    controller_files,
)
require(
    any(path.endswith(".dist-info/licenses/NOTICE") for path in controller_files),
    controller_files,
)

forbidden_paths = ("/tmp/release-inputs", "/tmp/s3-provider.lock", "/tmp/s3-provider-artifacts.lock")
require(all(not Path(path).exists() for path in forbidden_paths), forbidden_paths)

forbidden_environment = (
    "SYNAPSE_SECRETS_YAML",
    "SYNAPSE_SIGNING_KEY",
    "MAS_SECRETS_YAML",
    "DODO_API_KEY",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
)
require(all(name not in os.environ for name in forbidden_environment), forbidden_environment)
