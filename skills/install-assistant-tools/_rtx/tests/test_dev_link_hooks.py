"""Unit tests for registry-driven Claude/Codex hook installation."""

from __future__ import annotations

import json
import importlib.util
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _config_bridge as dev_link
else:
    import _config_bridge as dev_link  # noqa: E402
if __package__ and __package__.count('.') >= 1:
    from .._assistant_access_config import reconcile_assistant_access
    from .._state_record import Manifest
else:
    from _assistant_access_config import reconcile_assistant_access  # noqa: E402
    from _state_record import Manifest  # noqa: E402

from officina.common.famulus_paths import resolve_famulus_paths
from officina.install.context import InstallationContext


class DevLinkHooksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo_root = ROOT_DIR

    def test_install_claude_hooks_installs_registered_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp) / ".claude"
            dev_link.install_claude_hooks(claude_home, self.repo_root, dry_run=False)

            settings = json.loads((claude_home / "settings.local.json").read_text(encoding="utf-8"))
            session_start = settings["hooks"]["SessionStart"]
            commands = [hook["command"] for entry in session_start for hook in entry["hooks"]]

            self.assertTrue(commands)
            self.assertTrue(any("--claude" in command for command in commands))
            self.assertTrue(any("inject_dispatcher_context.py" in command and "llmhooks" in command for command in commands))

    def test_install_claude_hooks_replaces_legacy_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp) / ".claude"
            claude_home.mkdir(parents=True)
            legacy_command = f'python3 "{self.repo_root / "hooks" / "inject_dispatcher_context.py"}"'
            settings_file = claude_home / "settings.local.json"
            settings_file.write_text(
                json.dumps(
                    {
                        "hooks": {
                            "SessionStart": [
                                {"matcher": "startup|clear|compact", "hooks": [{"type": "command", "command": legacy_command}]}
                            ]
                        }
                    },
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )

            dev_link.install_claude_hooks(claude_home, self.repo_root, dry_run=False)

            settings = json.loads(settings_file.read_text(encoding="utf-8"))
            commands = [hook["command"] for entry in settings["hooks"]["SessionStart"] for hook in entry["hooks"]]
            self.assertNotIn(legacy_command, commands)
            self.assertTrue(any("--claude" in command for command in commands))

    def test_reinstall_preserves_a_user_hook_added_to_a_managed_entry_group(self) -> None:
        """Re-running install replaces its own hook objects, not the group.

        An entry group is shared structure: a user hook parked alongside the
        managed one must survive a reinstall, and the managed hook must not
        be duplicated.
        """
        with tempfile.TemporaryDirectory() as tmp:
            claude_home = Path(tmp) / ".claude"
            dev_link.install_claude_hooks(claude_home, self.repo_root, dry_run=False)
            settings_file = claude_home / "settings.local.json"

            payload = json.loads(settings_file.read_text(encoding="utf-8"))
            payload["hooks"]["SessionStart"][0]["hooks"].append(
                {"type": "command", "command": "echo my-own-hook"}
            )
            settings_file.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

            dev_link.install_claude_hooks(claude_home, self.repo_root, dry_run=False)

            after = json.loads(settings_file.read_text(encoding="utf-8"))
            commands = [
                hook["command"]
                for entry in after["hooks"]["SessionStart"]
                for hook in entry["hooks"]
            ]
            self.assertIn("echo my-own-hook", commands)
            managed = [c for c in commands if "inject_dispatcher_context.py" in c]
            self.assertEqual(len(managed), 1, commands)

    def test_install_codex_hooks_writes_managed_block_for_registered_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            dev_link.install_codex_hooks(codex_home, self.repo_root, dry_run=False)

            config_text = (codex_home / "config.toml").read_text(encoding="utf-8")
            self.assertIn(dev_link.HOOKS_BLOCK_BEGIN, config_text)
            self.assertIn("[[hooks.SessionStart]]", config_text)
            self.assertIn("--codex", config_text)
            self.assertIn("inject_dispatcher_context.py", config_text)
            self.assertIn("llmhooks", config_text)

    def test_install_codex_hooks_replaces_existing_managed_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            codex_home = Path(tmp) / ".codex"
            codex_home.mkdir(parents=True)
            config_file = codex_home / "config.toml"
            config_file.write_text(
                "user = 'keep'\n"
                f"{dev_link.HOOKS_BLOCK_BEGIN}\n"
                "[[hooks.SessionStart]]\n"
                'matcher = "startup|clear|compact"\n'
                "[[hooks.SessionStart.hooks]]\n"
                'type = "command"\n'
                f'command = "{self.repo_root / "hooks" / "inject_dispatcher_context.py"}"\n'
                f"{dev_link.HOOKS_BLOCK_END}\n",
                encoding="utf-8",
            )

            dev_link.install_codex_hooks(codex_home, self.repo_root, dry_run=False)

            config_text = config_file.read_text(encoding="utf-8")
            self.assertIn("user = 'keep'", config_text)
            self.assertIn("--codex", config_text)
            self.assertNotIn('/hooks/inject_dispatcher_context.py"', config_text)

    def test_access_reconcile_preserves_the_existing_codex_hook_block_exactly(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            codex_home = home / ".codex"
            dev_link.install_codex_hooks(codex_home, self.repo_root, dry_run=False)
            config = codex_home / "config.toml"
            before = config.read_bytes()
            context = InstallationContext(
                mode="standard",
                source_root=self.repo_root,
                development_root=None,
                paths=resolve_famulus_paths(platform=sys.platform, home=home, environ={}),
                selected_home=home,
                codex_home=codex_home,
                claude_home=home / ".claude",
                installation_id="standard",
            )
            manifest = Manifest(context.paths.install_state_root / "install-manifest.json")
            manifest.bind_context(mode="standard", installation_id="standard")

            reconcile_assistant_access(context, manifest)

            after = config.read_bytes()
            begin = before.index(dev_link.HOOKS_BLOCK_BEGIN.encode())
            end = before.index(dev_link.HOOKS_BLOCK_END.encode()) + len(
                dev_link.HOOKS_BLOCK_END.encode()
            )
            self.assertIn(before[begin:end], after)
            self.assertEqual(
                {entry["kind"] for entry in manifest.entries if entry["path"] == str(config)},
                {"codex_access_array_block"},
            )

    def test_hook_loaded_from_standard_release_uses_only_that_immutable_resource_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            resources = Path(tmp) / "release" / "launcher-resources"
            script = resources / "llmhooks" / "inject_dispatcher_context.py"
            script.parent.mkdir(parents=True)
            script.write_bytes((ROOT_DIR / "llmhooks" / "inject_dispatcher_context.py").read_bytes())
            (resources / "script_dispatcher" / "src" / "script_dispatcher").mkdir(parents=True)
            spec = importlib.util.spec_from_file_location("release_dispatcher_hook", script)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            original_which = shutil.which
            try:
                shutil.which = lambda name: "/managed/bin/dispatcher" if name == "dispatcher" else None
                self.assertEqual(module.dispatcher_available(), (True, []))
            finally:
                shutil.which = original_which
            self.assertEqual(module._REPO_ROOT, resources.resolve())

    def test_development_hook_binding_uses_exact_checkout_without_repository_walking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp) / "checkout with spaces"
            checkout.mkdir()
            binding = dev_link._hook_bindings(checkout, "claude")[0]

            self.assertEqual(
                binding.argv[1],
                str(checkout / "llmhooks" / "inject_dispatcher_context.py"),
            )


if __name__ == "__main__":
    unittest.main()
