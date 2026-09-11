#!/usr/bin/env python3
"""Offline tests for the Synapse staging entrypoint."""

from __future__ import annotations

import contextlib
import importlib.machinery
import importlib.util
import io
import os
import pathlib
import tempfile
import unittest
from unittest import mock


ROOT = pathlib.Path(__file__).resolve().parents[1]
LOADER = importlib.machinery.SourceFileLoader(
    "telecrypt_synapse_entrypoint", str(ROOT / "telecrypt-synapse-entrypoint")
)
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC is not None
entrypoint = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(entrypoint)


class EntrypointTests(unittest.TestCase):
    def test_prepare_creates_and_clears_disposable_directories(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = pathlib.Path(directory) / "staging"
            keep = staging / "keep"
            keep.mkdir(parents=True)
            (keep / "sentinel").write_text("retain", encoding="ascii")
            for child in ("tmp", "media"):
                child_path = staging / child
                child_path.mkdir()
                (child_path / "stale").mkdir()
                (child_path / "stale" / "payload").write_text("remove", encoding="ascii")
            original_tmpdir = os.environ.get("TMPDIR")
            try:
                entrypoint.prepare_staging(str(staging))
                self.assertEqual(os.environ["TMPDIR"], str(staging / "tmp"))
                self.assertEqual(list((staging / "tmp").iterdir()), [])
                self.assertEqual(list((staging / "media").iterdir()), [])
                self.assertEqual((keep / "sentinel").read_text(encoding="ascii"), "retain")
            finally:
                if original_tmpdir is None:
                    os.environ.pop("TMPDIR", None)
                else:
                    os.environ["TMPDIR"] = original_tmpdir

    def test_prepare_unlinks_symlinks_without_following_them(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = pathlib.Path(directory) / "staging"
            outside = pathlib.Path(directory) / "outside"
            outside.mkdir()
            (outside / "sentinel").write_text("retain", encoding="ascii")
            temporary = staging / "tmp"
            temporary.mkdir(parents=True)
            (temporary / "outside").symlink_to(outside, target_is_directory=True)
            (staging / "media").symlink_to(outside, target_is_directory=True)
            entrypoint.prepare_staging(str(staging))
            self.assertEqual((outside / "sentinel").read_text(encoding="ascii"), "retain")
            self.assertEqual(list(temporary.iterdir()), [])
            self.assertFalse((staging / "media").is_symlink())
            self.assertEqual(list((staging / "media").iterdir()), [])

    def test_main_executes_synapse(self) -> None:
        with mock.patch.object(entrypoint, "prepare_staging"), mock.patch.object(
            entrypoint.os, "execv"
        ) as execute:
            entrypoint.main(["-c", "/homeserver.yaml"])
        execute.assert_called_once_with(
            entrypoint.sys.executable,
            [entrypoint.sys.executable, "-m", "synapse.app.homeserver", "-c", "/homeserver.yaml"],
        )

    def test_main_requires_arguments(self) -> None:
        with mock.patch.object(entrypoint, "prepare_staging"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                entrypoint.main([])


if __name__ == "__main__":
    unittest.main()
