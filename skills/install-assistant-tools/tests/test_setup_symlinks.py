"""Unit tests for setup_symlinks.py using isolated temp directories.

These tests are meant to catch real behavioral regressions: dry-run semantics,
conflict preservation, symlink replacement, and the codex-home symlink guard.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT_DIR / "tests"))
import setup_symlinks  # noqa: E402
from install_test_utils import can_create_symlink  # noqa: E402


class SetupSymlinksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not can_create_symlink():
            raise unittest.SkipTest("symlink creation is unavailable on this machine")

    def setUp(self) -> None:
        self.repo_root = Path(setup_symlinks.__file__).resolve().parents[3]

    def capture_run(self, **kwargs: object) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            setup_symlinks.run(**kwargs)
        return buf.getvalue()

    def test_creates_expected_links_in_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude_home = home / "claude"
            codex_home = home / "codex"

            self.capture_run(
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                dry_run=False,
            )

            claude_expected = {
                claude_home / "skills": self.repo_root / "skills",
                claude_home / "references": self.repo_root / "references",
                claude_home / "agents": self.repo_root / "agents",
                claude_home / "CLAUDE.md": self.repo_root / "CLAUDE.md",
            }
            codex_expected = {
                codex_home / "references": self.repo_root / "references",
                codex_home / "agents": self.repo_root / "agents",
                codex_home / "AGENTS.md": (self.repo_root / "CLAUDE.md").resolve(),
                codex_home / "assistant.config.toml": self.repo_root / "profiles" / "assistant.config.toml",
                codex_home / "collab.config.toml": self.repo_root / "profiles" / "collab.config.toml",
                codex_home / "coauthor.config.toml": self.repo_root / "profiles" / "coauthor.config.toml",
            }

            for path, target in claude_expected.items():
                self.assertTrue(path.is_symlink(), path)
                self.assertEqual(path.resolve(), target.resolve())

            for path, target in codex_expected.items():
                self.assertTrue(path.is_symlink(), path)
                expected = target if target.is_absolute() else target.resolve()
                self.assertEqual(path.resolve(), expected)

    def test_dry_run_does_not_create_any_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude_home = home / "claude"
            codex_home = home / "codex"

            output = self.capture_run(
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                dry_run=True,
            )

            self.assertIn("Would link", output)
            self.assertFalse(claude_home.exists())
            self.assertFalse(codex_home.exists())

    def test_existing_real_paths_are_preserved_and_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude_home = home / "claude"
            codex_home = home / "codex"
            claude_home.mkdir()
            codex_home.mkdir()

            existing_skills = claude_home / "skills"
            existing_skills.mkdir()
            existing_profile = codex_home / "assistant.config.toml"
            existing_profile.write_text("machine-local", encoding="utf-8")

            output = self.capture_run(
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                dry_run=False,
            )

            self.assertIn("SKIP (already exists as real path, not a symlink)", output)
            self.assertTrue(existing_skills.is_dir())
            self.assertFalse(existing_skills.is_symlink())
            self.assertEqual(existing_profile.read_text(encoding="utf-8"), "machine-local")
            self.assertFalse(existing_profile.is_symlink())

    def test_existing_symlink_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            claude_home = home / "claude"
            claude_home.mkdir()
            old_target = home / "old-skills"
            old_target.mkdir()
            skills_link = claude_home / "skills"
            skills_link.symlink_to(old_target)

            self.capture_run(
                home=home,
                claude_home=claude_home,
                do_claude=True,
                do_codex=False,
                dry_run=False,
            )

            self.assertTrue(skills_link.is_symlink())
            self.assertEqual(skills_link.resolve(), (self.repo_root / "skills").resolve())

    def test_codex_home_symlink_boundary_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            real_target = home / "real-codex-home"
            real_target.mkdir()
            codex_home = home / "codex-home"
            codex_home.symlink_to(real_target)

            output = self.capture_run(
                home=home,
                codex_home=codex_home,
                do_claude=False,
                do_codex=True,
                dry_run=False,
            )

            self.assertIn("is a symlink, not a real directory", output)
            self.assertFalse((real_target / "references").exists())
            self.assertFalse((real_target / "agents").exists())


if __name__ == "__main__":
    unittest.main()
