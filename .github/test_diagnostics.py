#!/usr/bin/env python3
"""Offline tests for command diagnostic classification."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "check_diagnostics.sh"


class DiagnosticsTests(unittest.TestCase):
    def run_check(self, content: bytes) -> subprocess.CompletedProcess[str]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics"
            path.write_bytes(content)
            return subprocess.run(
                ["bash", str(SCRIPT), str(path)],
                cwd=ROOT,
                env=os.environ.copy(),
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )

    def test_accepts_and_surfaces_ordinary_progress(self) -> None:
        result = self.run_check(b"#1 exporting image\n#1 DONE\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("#1 DONE", result.stderr)

    def test_reports_warning_and_error_markers_without_changing_success(self) -> None:
        for diagnostics in (b"WARNING: a non-fatal warning\n", b"ERROR: command failed\n"):
            with self.subTest(diagnostics=diagnostics):
                result = self.run_check(diagnostics)
                self.assertEqual(result.returncode, 0)
                self.assertEqual(result.stderr.encode(), diagnostics)
                self.assertNotIn("warning or error diagnostics", result.stderr)

    def test_accepts_complete_large_diagnostics(self) -> None:
        diagnostics = b"x" * (1024 * 1024)
        result = self.run_check(diagnostics)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.encode(), diagnostics)


if __name__ == "__main__":
    unittest.main()
