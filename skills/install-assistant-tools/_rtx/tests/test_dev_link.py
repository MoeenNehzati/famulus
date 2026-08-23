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
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from test_support.git_repository import GitTestRepository

ROOT_DIR = Path(__file__).resolve().parents[4]
SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(ROOT_DIR / "tests"))
if __package__ and __package__.count('.') >= 1:
    from .. import _config_bridge as dev_link
else:
    import _config_bridge as dev_link  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .._state_record import Manifest
else:
    from _state_record import Manifest  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .install_test_utils import can_create_symlink
else:
    from install_test_utils import can_create_symlink  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .._fs_links import default_bin_dir
else:
    from _fs_links import default_bin_dir  # noqa: E402


def _raw_git_config(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    # famulus-raw-git: category=hooks; reason=the test must observe the configured hooksPath without the shared helper's hooksPath override
    return subprocess.run(
        ["git", "-C", str(repo_root), "config", *args],
        capture_output=True,
        check=check,
    )


class SetupSymlinksTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        if not can_create_symlink():
            # famulus-skip: category=capability-unavailable; reason=dev-link tests verify symlink layout; alternate=installer launcher tests cover copy-based Windows behavior
            raise unittest.SkipTest("symlink creation is unavailable on this machine")

    def capture_run(self, **kwargs: object) -> str:
        buf = io.StringIO()
        with redirect_stdout(buf):
            dev_link.run(**kwargs)
        return buf.getvalue()

    def make_repo_root(self, base: Path) -> Path:
        repo_root = base / "repo"
        GitTestRepository.create(repo_root)
        (repo_root / "skills").mkdir(parents=True)
        (repo_root / "references").mkdir()
        (repo_root / "agents").mkdir()
        (repo_root / "profiles").mkdir()
        (repo_root / ".githooks").mkdir()
        (repo_root / "llmhooks").mkdir()
        runtime_dir = repo_root / "skills" / "milestone-logging" / "_rtx"
        runtime_dir.mkdir(parents=True)
        source_runtime_dir = ROOT_DIR / "skills" / "milestone-logging" / "_rtx"
        for helper in ("_milestone_writer.py", "_agent_timeline.py"):
            shutil.copy2(
                source_runtime_dir / helper,
                runtime_dir / helper,
            )
        compatibility_dir = (
            repo_root / "skills" / "install-assistant-tools" / "_rtx" / "assets" / "bin"
        )
        compatibility_dir.mkdir(parents=True)
        source_compatibility_dir = SCRIPT_DIR / "assets" / "bin"
        for helper in ("milestone", "agent-timeline"):
            shutil.copy2(
                source_compatibility_dir / helper,
                compatibility_dir / helper,
            )
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
        return repo_root

    # famulus-skip: category=platform-contract; reason=Windows cannot execute extension-less links, so dev_link skips these helpers there; alternate=none needed until .bat wrappers exist
    @unittest.skipIf(sys.platform == "win32", "milestone helpers are POSIX-only by design")
    def test_installs_milestone_helpers_into_bin_dir(self) -> None:
        """Installer co-delivers compatibility commands with root instructions."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            manifest = Manifest(Path(tmp) / "manifest.json")

            self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=home / "claude",
                codex_home=home / "codex",
                dry_run=False,
                manifest=manifest,
            )

            bin_dir = default_bin_dir(home=home)
            compatibility_dir = (
                repo_root
                / "skills"
                / "install-assistant-tools"
                / "_rtx"
                / "assets"
                / "bin"
            )
            expected = {
                bin_dir / "milestone": compatibility_dir / "milestone",
                bin_dir / "agent-timeline": compatibility_dir / "agent-timeline",
            }
            for link, target in expected.items():
                self.assertTrue(link.is_symlink(), f"missing link: {link}")
                self.assertEqual(Path(os.readlink(link)), target)

            # Recorded so uninstall removes them with everything else.
            recorded = {
                entry.get("path")
                for entry in manifest.entries
                if entry.get("kind") == "symlink"
            }
            for link in expected:
                self.assertIn(str(link), recorded)

    # famulus-skip: category=platform-contract; reason=Windows cannot execute extension-less links, so dev_link skips these helpers there; alternate=none needed until .bat wrappers exist
    @unittest.skipIf(sys.platform == "win32", "milestone helpers are POSIX-only by design")
    def test_installed_milestone_helpers_execute_without_activation_imports(self) -> None:
        """Installed links run without activation-provided Python import state."""
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            self.capture_run(
                repo_root=repo_root,
                home=home,
                claude_home=home / "claude",
                codex_home=home / "codex",
                dry_run=False,
            )

            bin_dir = default_bin_dir(home=home)
            dispatcher = bin_dir / "dispatcher"
            dispatcher.write_text(
                "#!/bin/sh\n"
                f"PYTHONPATH={shlex.quote(str(ROOT_DIR / 'src'))} "
                f"exec {shlex.quote(sys.executable)} -P -m officina.dispatcher.cli "
                f"--repository-config {shlex.quote(str(ROOT_DIR / 'officina.toml'))} "
                '"$@"\n',
                encoding="utf-8",
            )
            dispatcher.chmod(0o755)
            environment = os.environ.copy()
            for variable in (
                "PYTHONPATH",
                "PYTHONHOME",
                "VIRTUAL_ENV",
                "CONDA_PREFIX",
                "CONDA_DEFAULT_ENV",
            ):
                environment.pop(variable, None)
            environment["PATH"] = os.pathsep.join(
                (str(bin_dir), environment.get("PATH", ""))
            )
            environment.update(
                {
                    "ASSISTANT_LOGS": str(Path(tmp) / "logs"),
                    "CODEX_SESSION_ID": "installed-launcher-session",
                    "CODEX_THREAD_ID": "installed-launcher-agent",
                }
            )

            try:
                milestone = subprocess.run(
                    [str(bin_dir / "milestone"), "--path"],
                    env=environment,
                    cwd=repo_root,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="strict",
                )
            except PermissionError as exc:
                self.fail(f"installed milestone launcher is not executable: {exc}")
            self.assertEqual(milestone.returncode, 0, milestone.stderr)
            milestone_path = Path(milestone.stdout.strip())
            self.assertTrue(
                milestone_path.is_relative_to(Path(environment["ASSISTANT_LOGS"])),
                milestone.stdout,
            )

            record = subprocess.run(
                [
                    str(bin_dir / "milestone"),
                    "--role",
                    "sanitized launcher test",
                    "record through installed launcher",
                    "",
                ],
                env=environment,
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            self.assertEqual(record.returncode, 0, record.stderr)

            timeline = subprocess.run(
                [str(bin_dir / "agent-timeline"), "--list"],
                env=environment,
                cwd=repo_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
            )
            self.assertEqual(timeline.returncode, 0, timeline.stderr)
            self.assertIn("installed-launcher-session", timeline.stdout)

    def test_creates_expected_links_in_empty_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            (repo_root / "skills" / "proof-audit").mkdir()
            (repo_root / "skills" / "proof-audit" / "SKILL.md").write_text(
                "# proof audit\n", encoding="utf-8"
            )
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
            }
            codex_expected = {
                codex_home / "references": repo_root / "references",
                codex_home / "agents": repo_root / "agents",
                codex_home / "assistant.config.toml": repo_root / "profiles" / "assistant.config.toml",
                codex_home / "collab.config.toml": repo_root / "profiles" / "collab.config.toml",
                codex_home / "coauthor.config.toml": repo_root / "profiles" / "coauthor.config.toml",
            }
            if sys.platform != "win32":
                claude_expected[claude_home / "CLAUDE.md"] = repo_root / "CLAUDE.md"
                codex_expected[codex_home / "AGENTS.md"] = (repo_root / "CLAUDE.md").resolve()

            for path, target in claude_expected.items():
                self.assertTrue(path.is_symlink(), path)
                self.assertEqual(path.resolve(), target.resolve())

            for path, target in codex_expected.items():
                self.assertTrue(path.is_symlink(), path)
                self.assertEqual(path.resolve(), target.resolve())

            self.assertTrue((codex_home / "skills").is_dir())
            self.assertFalse((codex_home / "skills").is_symlink())
            self.assertTrue((codex_home / "skills" / "proof-audit").is_symlink())
            self.assertEqual(
                (codex_home / "skills" / "proof-audit").resolve(),
                (repo_root / "skills" / "proof-audit").resolve(),
            )

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

    def test_codex_skills_directory_preserves_system_and_links_repo_skills(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_root = self.make_repo_root(base)
            (repo_root / "skills" / "proof-audit").mkdir()
            (repo_root / "skills" / "proof-audit" / "SKILL.md").write_text(
                "# proof audit\n", encoding="utf-8"
            )
            home = base / "home"
            codex_home = home / "codex"
            skills_dir = codex_home / "skills"
            skills_dir.mkdir(parents=True)
            (skills_dir / "proof-audit").symlink_to(repo_root / "skills" / "proof-audit")
            (skills_dir / ".system").mkdir()
            (skills_dir / ".system" / "keep.txt").write_text("system\n", encoding="utf-8")
            manifest = Manifest(base / "manifest.json")
            manifest.record(
                "symlink", path=str(skills_dir), target=str(repo_root / "skills")
            )

            output = self.capture_run(
                home=home,
                repo_root=repo_root,
                codex_home=codex_home,
                do_claude=False,
                do_codex=True,
                dry_run=False,
                manifest=manifest,
            )

            self.assertIn("OK (already linked)", output)
            self.assertTrue(skills_dir.is_dir())
            self.assertFalse(skills_dir.is_symlink())
            self.assertTrue((skills_dir / ".system").is_dir())
            self.assertEqual(
                (skills_dir / ".system" / "keep.txt").read_text(encoding="utf-8"),
                "system\n",
            )
            self.assertTrue((skills_dir / "proof-audit").is_symlink())
            self.assertEqual(
                (skills_dir / "proof-audit").resolve(),
                (repo_root / "skills" / "proof-audit").resolve(),
            )
            self.assertFalse((repo_root / "skills" / ".system").exists())
            skill_links = {
                entry["path"]
                for entry in manifest.entries
                if entry.get("kind") == "symlink"
            }
            self.assertNotIn(str(skills_dir), skill_links)
            self.assertIn(str(skills_dir / "proof-audit"), skill_links)

    def test_codex_legacy_top_level_skills_link_is_converted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_root = self.make_repo_root(base)
            skill = repo_root / "skills" / "proof-audit"
            skill.mkdir()
            (skill / "SKILL.md").write_text("# proof audit\n", encoding="utf-8")
            home = base / "home"
            codex_home = home / "codex"
            codex_home.mkdir(parents=True)
            skills_dir = codex_home / "skills"
            skills_dir.symlink_to(repo_root / "skills")

            self.capture_run(
                home=home,
                repo_root=repo_root,
                codex_home=codex_home,
                do_claude=False,
                do_codex=True,
                dry_run=False,
            )

            self.assertTrue(skills_dir.is_dir())
            self.assertFalse(skills_dir.is_symlink())
            self.assertTrue((skills_dir / "proof-audit").is_symlink())
            self.assertEqual((skills_dir / "proof-audit").resolve(), skill.resolve())

    def test_codex_local_skill_conflict_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo_root = self.make_repo_root(base)
            repo_skill = repo_root / "skills" / "proof-audit"
            repo_skill.mkdir()
            (repo_skill / "SKILL.md").write_text("repo\n", encoding="utf-8")
            home = base / "home"
            codex_home = home / "codex"
            local_skill = codex_home / "skills" / "proof-audit"
            local_skill.mkdir(parents=True)
            (local_skill / "SKILL.md").write_text("local\n", encoding="utf-8")

            output = self.capture_run(
                home=home,
                repo_root=repo_root,
                codex_home=codex_home,
                do_claude=False,
                do_codex=True,
                dry_run=False,
            )

            self.assertIn("SKIP (local Codex skill conflicts with repo skill): proof-audit", output)
            self.assertFalse(local_skill.is_symlink())
            self.assertEqual((local_skill / "SKILL.md").read_text(encoding="utf-8"), "local\n")

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
            with self.assertRaises(ValueError):
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

            result = _raw_git_config(repo_root, "core.hooksPath")
            self.assertEqual(
                result.stdout.decode("utf-8").strip(),
                ".githooks",
            )

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

            with mock.patch.dict(
                os.environ,
                {
                    "GIT_DIR": str(ROOT_DIR / ".git"),
                    "GIT_WORK_TREE": str(ROOT_DIR),
                },
            ):
                output = self.capture_run(
                    repo_root=repo_root,
                    home=home,
                    claude_home=claude_home,
                    codex_home=codex_home,
                    dry_run=False,
                )

            self.assertIn("not a git checkout; skipping git hooks setup", output)

    def test_run_does_not_persist_ai_or_logs_in_rc_file(self) -> None:
        if sys.platform == "win32":
            # famulus-skip: category=platform-contract; reason=Windows has no POSIX rc-file path to inspect; alternate=test_run_does_not_persist_ai_or_logs_in_windows_registry
            self.skipTest("POSIX rc-file assertion")
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
            self.assertNotIn("AI=", content)
            self.assertNotIn("ASSISTANT_LOGS=", content)
            self.assertNotIn("ASSISTANT_DEFAULT", content)  # dev_link does not own this var

    def test_run_does_not_persist_ai_or_logs_in_windows_registry(self) -> None:
        registry_calls = []

        class FakeKey:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

        fake_winreg = types.SimpleNamespace(
            HKEY_CURRENT_USER=object(),
            KEY_READ=1,
            KEY_WRITE=2,
            REG_SZ=1,
            OpenKey=lambda *args, **kwargs: FakeKey(),
            SetValueEx=lambda key, name, reserved, kind, value: registry_calls.append(
                (name, kind, value)
            ),
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo_root = self.make_repo_root(Path(tmp))
            home = Path(tmp) / "home"
            home.mkdir()
            rc_file = home / ".bashrc"
            rc_file.write_text("")
            claude_home = home / "claude"
            codex_home = home / "codex"

            with mock.patch.object(dev_link.sys, "platform", "win32"), mock.patch.dict(
                sys.modules, {"winreg": fake_winreg}
            ):
                self.capture_run(
                    repo_root=repo_root,
                    home=home,
                    claude_home=claude_home,
                    codex_home=codex_home,
                    shell_rc=rc_file,
                    dry_run=False,
                )

            self.assertEqual(registry_calls, [])
            self.assertEqual(rc_file.read_text(), "")


if __name__ == "__main__":
    unittest.main()
