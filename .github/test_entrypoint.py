#!/usr/bin/env python3
"""Offline tests for the fixed Synapse staging entrypoint."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import contextlib
import io
import os
import pathlib
import stat
import tempfile
import unittest
from types import SimpleNamespace
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
    def mountinfo(self, path: pathlib.Path, filesystem: str = "ext4", options: str = "rw") -> pathlib.Path:
        mountinfo = path.parent / "mountinfo"
        mountinfo.write_text(
            f"42 1 8:1 / {path} {options},relatime - {filesystem} /dev/sda {options},relatime\n",
            encoding="ascii",
        )
        return mountinfo

    def prepare(self, path: pathlib.Path, *, reserve: int = 1) -> None:
        mountinfo = self.mountinfo(path)
        uid = path.stat().st_uid
        gid = path.stat().st_gid
        with (
            mock.patch.object(entrypoint, "EXPECTED_UID", uid),
            mock.patch.object(entrypoint, "EXPECTED_GID", gid),
            mock.patch.object(entrypoint, "FREE_RESERVE_BYTES", reserve),
        ):
            entrypoint.prepare_staging(staging=str(path), mountinfo_path=str(mountinfo))

    def test_script_is_fixed_and_executable(self) -> None:
        script = ROOT / "telecrypt-synapse-entrypoint"
        self.assertEqual(script.read_text(encoding="utf-8").splitlines()[0], "#!/usr/local/bin/python")
        self.assertEqual(stat.S_IMODE(script.stat().st_mode), 0o755)
        source = script.read_text(encoding="utf-8")
        for value in (
            'STAGING_PATH = "/staging"',
            'TMP_PATH = f"{STAGING_PATH}/tmp"',
            'MEDIA_PATH = f"{STAGING_PATH}/media"',
            "EXPECTED_STAGING_MODE = 0o711",
            "EXPECTED_CHILD_MODE = 0o700",
            "FREE_RESERVE_BYTES = 10 * 1024 * 1024 * 1024",
        ):
            self.assertIn(value, source)

    def test_mount_parser_accepts_one_writable_disk_mount(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "staging"
            path.mkdir(mode=0o700)
            mountinfo = self.mountinfo(path)
            entrypoint.validate_mount(str(path), str(mountinfo))

    def test_mount_parser_rejects_unmounted_memory_or_read_only_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "staging"
            path.mkdir(mode=0o700)
            for filesystem, options in (("tmpfs", "rw"), ("ext4", "ro"), ("overlay", "rw")):
                with self.subTest(filesystem=filesystem, options=options):
                    mountinfo = self.mountinfo(path, filesystem, options)
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        entrypoint.validate_mount(str(path), str(mountinfo))

            duplicate = path.parent / "duplicate-mountinfo"
            duplicate.write_text(
                mountinfo.read_text(encoding="ascii") + mountinfo.read_text(encoding="ascii"),
                encoding="ascii",
            )
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                entrypoint.validate_mount(str(path), str(duplicate))

    def test_prepare_clears_only_tmp_and_media_children_and_sets_tmpdir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = pathlib.Path(directory) / "staging"
            staging.mkdir(mode=0o711)
            (staging / "keep").mkdir(mode=0o700)
            (staging / "keep" / "sentinel").write_text("retain", encoding="ascii")
            for child in ("tmp", "media"):
                child_path = staging / child
                child_path.mkdir(mode=0o700)
                (child_path / "stale").mkdir(mode=0o700)
                (child_path / "stale" / "payload").write_text("remove", encoding="ascii")
            original_tmpdir = os.environ.get("TMPDIR")
            try:
                self.prepare(staging)
                self.assertEqual(os.environ["TMPDIR"], str(staging / "tmp"))
                self.assertEqual(list((staging / "tmp").iterdir()), [])
                self.assertEqual(list((staging / "media").iterdir()), [])
                self.assertEqual((staging / "keep" / "sentinel").read_text(encoding="ascii"), "retain")
            finally:
                if original_tmpdir is None:
                    os.environ.pop("TMPDIR", None)
                else:
                    os.environ["TMPDIR"] = original_tmpdir

    def test_prepare_requires_exact_distinct_root_and_child_modes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = pathlib.Path(directory) / "staging"
            staging.mkdir(mode=0o711)
            (staging / "tmp").mkdir(mode=0o700)
            (staging / "media").mkdir(mode=0o700)

            original_tmpdir = os.environ.get("TMPDIR")
            try:
                self.prepare(staging)
                self.assertEqual(stat.S_IMODE(staging.stat().st_mode), 0o711)
                for child in ("tmp", "media"):
                    self.assertEqual(stat.S_IMODE((staging / child).stat().st_mode), 0o700)

                for path, mode in (
                    (staging, 0o700),
                    (staging, 0o755),
                    (staging / "tmp", 0o711),
                    (staging / "media", 0o755),
                ):
                    with self.subTest(path=path.name, mode=oct(mode)):
                        path.chmod(mode)
                        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                            self.prepare(staging)
                        path.chmod(0o711 if path == staging else 0o700)
            finally:
                if original_tmpdir is None:
                    os.environ.pop("TMPDIR", None)
                else:
                    os.environ["TMPDIR"] = original_tmpdir

    def test_prepare_rejects_symlinked_staging_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = pathlib.Path(directory) / "staging"
            staging.mkdir(mode=0o711)
            outside = pathlib.Path(directory) / "outside"
            outside.write_text("retain", encoding="ascii")
            (staging / "tmp").symlink_to(outside)
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                self.prepare(staging)
            self.assertEqual(outside.read_text(encoding="ascii"), "retain")

    def test_free_space_reserve_is_exact_and_fail_closed(self) -> None:
        with mock.patch.object(entrypoint, "FREE_RESERVE_BYTES", 10), mock.patch.object(
            entrypoint.os, "statvfs", return_value=SimpleNamespace(f_bavail=9, f_frsize=1)
        ):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                entrypoint._free_bytes("/staging")

        with mock.patch.object(entrypoint, "FREE_RESERVE_BYTES", 10), mock.patch.object(
            entrypoint.os, "statvfs", return_value=SimpleNamespace(f_bavail=10, f_frsize=1)
        ):
            self.assertEqual(entrypoint._free_bytes("/staging"), 10)

    def test_main_executes_synapse_after_preparation(self) -> None:
        with mock.patch.object(entrypoint, "prepare_staging"), mock.patch.object(
            entrypoint.os, "execv"
        ) as execv:
            entrypoint.main(["-c", "/homeserver.yaml"])
        executable = execv.call_args.args[0]
        command = execv.call_args.args[1]
        self.assertEqual(command, [executable, "-m", "synapse.app.homeserver", "-c", "/homeserver.yaml"])

    def test_main_rejects_missing_synapse_arguments(self) -> None:
        with mock.patch.object(entrypoint, "prepare_staging"):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                entrypoint.main([])


if __name__ == "__main__":
    unittest.main()
