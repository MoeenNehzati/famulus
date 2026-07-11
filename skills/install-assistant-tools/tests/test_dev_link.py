"""Unit tests for dev_link.py using isolated temp directories.

These tests are meant to catch real behavioral regressions: dry-run semantics,
conflict preservation, symlink replacement, and the codex-home symlink guard.

Every test builds its own throwaway repo_root (via make_repo_root) rather
than using the live checkout this test file lives in — dev_link.run() now
also writes `git config core.hooksPath` and dev-mode hook registrations,
and running that against the real repo during a test run would mutate live
repo state.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[3]
SCRIPT_DIR = Path(__file__).resolve().parents[1] / "_rtx"
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT_DIR / "tests"))
import _config_bridge as dev_link  # noqa: E402
from install_test_utils import can_create_symlink  # noqa: E402


class SetupSymlinksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not can_create_symlink():
            raise unittest.SkipTest("symlink creation is unavailable on this machine")

    def capture_run(self, **kwargs: object) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            dev_link.run(**kwargs)
        return buf.getvalue()

    def make_repo_root(self, base: Path) -> Path:
        repo_root = base / "repo"
        (repo_root / "skills").mkdir(parents=True)
        (repo_root / "references").mkdir()
        (repo_root / "agents").mkdir()
        (repo_root / "profiles").mkdir()
        (repo_root / ".githooks").mkdir()
        (repo_root / "llmhooks").mkdir()
        (repo_root / "llmhooks" / "registry.py").write_text(
            "def hooks_for_host(host):\n    return []\n", encoding="utf-8"
        )
        (repo_root / "CLAUDE.md").write_text("repo instructions\n", encoding="utf-8")
        (repo_root / "AGENTS.md").symlink_to(repo_root / "CLAUDE.md")
        for name in ("assistant", "collab", "coauthor"):
            (repo_root / "profiles" / f"{name}.config.toml").write_text(
                f"name = {name!r}\n",
                encoding="utf-8",
            )
        subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
        return repo_root

    def test_creates_expected_links_in_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            claude_home = home / "claude"
            codex_home = home / "codex"

            self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                dry_run=False,
            )

            claude_expected = {
                claude_home / "skills": repo_root / "skills",
                claude_home / "references": repo_root / "references",
                claude_home / "agents": repo_root / "agents",
                claude_home / "CLAUDE.md": repo_root / "CLAUDE.md",
            }
            codex_expected = {
                codex_home / "skills": repo_root / "skills",
                codex_home / "references": repo_root / "references",
                codex_home / "agents": repo_root / "agents",
                codex_home / "AGENTS.md": (repo_root / "CLAUDE.md").resolve(),
                codex_home / "assistant.config.toml": repo_root / "profiles" / "assistant.config.toml",
                codex_home / "collab.config.toml": repo_root / "profiles" / "collab.config.toml",
                codex_home / "coauthor.config.toml": repo_root / "profiles" / "coauthor.config.toml",
            }

            for path, target in claude_expected.items():
                self.assertTrue(path.is_symlink(), path)
                self.assertEqual(path.resolve(), target.resolve())

            for path, target in codex_expected.items():
                self.assertTrue(path.is_symlink(), path)
                self.assertEqual(path.resolve(), target.resolve())

    def test_dry_run_does_not_create_any_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            claude_home = home / "claude"
            codex_home = home / "codex"

            output = self.capture_run(
                repo_root=repo_root,
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
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            claude_home = home / "claude"
            codex_home = home / "codex"
            claude_home.mkdir(parents=True)
            codex_home.mkdir(parents=True)

            existing_references = claude_home / "references"
            existing_references.mkdir()
            existing_profile = codex_home / "assistant.config.toml"
            existing_profile.write_text("machine-local", encoding="utf-8")

            output = self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                dry_run=False,
            )

            self.assertIn("SKIP (already exists as real path, not a symlink)", output)
            self.assertTrue(existing_references.is_dir())
            self.assertFalse(existing_references.is_symlink())
            self.assertEqual(existing_profile.read_text(encoding="utf-8"), "machine-local")
            self.assertFalse(existing_profile.is_symlink())

    def test_existing_symlink_is_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            claude_home = home / "claude"
            claude_home.mkdir(parents=True)
            old_target = home / "old-skills"
            old_target.mkdir()
            skills_link = claude_home / "skills"
            skills_link.symlink_to(old_target)

            self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=claude_home,
                do_claude=True,
                do_codex=False,
                dry_run=False,
            )

            self.assertTrue(skills_link.is_symlink())
            self.assertEqual(skills_link.resolve(), (repo_root / "skills").resolve())

    def test_existing_correct_symlink_is_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            claude_home = home / "claude"
            claude_home.mkdir(parents=True)
            skills_link = claude_home / "skills"
            skills_link.symlink_to(repo_root / "skills")

            output = self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=claude_home,
                do_claude=True,
                do_codex=False,
                dry_run=False,
            )

            self.assertIn("OK (already linked)", output)
            self.assertTrue(skills_link.is_symlink())
            self.assertEqual(skills_link.resolve(), (repo_root / "skills").resolve())

    def test_existing_skills_directory_is_migrated_and_linked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_root = self.make_repo_root(base)
            (repo_root / "skills" / "proof-audit").mkdir()
            home = base / "home"
            codex_home = home / "codex"
            skills_dir = codex_home / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "proof-audit").symlink_to(repo_root / "skills" / "proof-audit")
            (skills_dir / ".system").mkdir()
            (skills_dir / ".system" / "keep.txt").write_text("system\n", encoding="utf-8")

            output = self.capture_run(
                home=home,
                repo_root=repo_root,
                codex_home=codex_home,
                do_claude=False,
                do_codex=True,
                dry_run=False,
            )

            self.assertIn("Removed redundant skill entry: proof-audit", output)
            self.assertIn("Preserved local skill entry: .system", output)
            self.assertTrue(skills_dir.is_symlink())
            self.assertEqual(skills_dir.resolve(), (repo_root / "skills").resolve())
            self.assertTrue((repo_root / "skills" / ".system").is_dir())
            self.assertEqual(
                (repo_root / "skills" / ".system" / "keep.txt").read_text(encoding="utf-8"),
                "system\n",
            )
            exclude_path = repo_root / ".git" / "info" / "exclude"
            self.assertIn("skills/.system", exclude_path.read_text(encoding="utf-8"))

    def test_skills_directory_conflict_is_left_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_root = self.make_repo_root(base)
            (repo_root / "skills" / "proof-audit").mkdir()
            (repo_root / "skills" / "proof-audit" / "repo.txt").write_text("repo\n", encoding="utf-8")
            home = base / "home"
            claude_home = home / "claude"
            skills_dir = claude_home / "skills"
            (skills_dir / "proof-audit").mkdir(parents=True)
            (skills_dir / "proof-audit" / "local.txt").write_text("local\n", encoding="utf-8")

            output = self.capture_run(
                home=home,
                repo_root=repo_root,
                claude_home=claude_home,
                do_claude=True,
                do_codex=False,
                dry_run=False,
            )

            self.assertIn("SKIP (skills directory has conflicting entries; resolve manually)", output)
            self.assertTrue(skills_dir.is_dir())
            self.assertFalse(skills_dir.is_symlink())
            self.assertTrue((skills_dir / "proof-audit" / "local.txt").exists())
            self.assertFalse((repo_root / "skills" / "proof-audit" / "local.txt").exists())

    def test_codex_home_symlink_boundary_is_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            real_target = home / "real-codex-home"
            real_target.mkdir(parents=True)
            codex_home = home / "codex-home"
            codex_home.symlink_to(real_target)

            output = self.capture_run(
                repo_root=repo_root,
                home=home,
                codex_home=codex_home,
                do_claude=False,
                do_codex=True,
                dry_run=False,
            )

            self.assertIn("is a symlink, not a real directory", output)
            self.assertFalse((real_target / "references").exists())
            self.assertFalse((real_target / "agents").exists())

    def test_run_requires_explicit_repo_root(self) -> None:
        # repo_root is now a required kwarg — calling without it must fail
        # loudly rather than silently deriving a path from this script's own
        # location.
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TypeError):
                dev_link.run(home=Path(tmp))  # missing required repo_root

    def test_run_installs_git_hooks_when_repo_is_git_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            claude_home = home / "claude"
            codex_home = home / "codex"

            self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                dry_run=False,
            )

            result = subprocess.run(
                ["git", "-C", str(repo_root), "config", "core.hooksPath"],
                capture_output=True, text=True,
            )
            self.assertEqual(result.stdout.strip(), ".githooks")

    def test_run_skips_git_hooks_when_repo_root_is_not_a_git_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = Path(tmp) / "repo"
            (repo_root / ".githooks").mkdir(parents=True)
            (repo_root / "llmhooks").mkdir()
            (repo_root / "llmhooks" / "registry.py").write_text(
                "def hooks_for_host(host):\n    return []\n", encoding="utf-8"
            )
            home = Path(tmp) / "home"
            claude_home = home / "claude"
            codex_home = home / "codex"

            output = self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                dry_run=False,
            )

            self.assertIn("not a git checkout; skipping git hooks setup", output)

    def test_run_sets_ai_in_rc_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            home.mkdir()
            rc_file = home / ".bashrc"
            rc_file.write_text("")
            claude_home = home / "claude"
            codex_home = home / "codex"

            self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=claude_home,
                codex_home=codex_home,
                shell_rc=rc_file,
                dry_run=False,
            )

            content = rc_file.read_text()
            self.assertIn(f'export AI="{repo_root}"', content)
            self.assertNotIn("ASSISTANT_DEFAULT", content)  # dev_link does not own this var


if __name__ == "__main__":
    unittest.main()
