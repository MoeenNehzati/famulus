from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import setup_tools  # noqa: E402


class SetupCloudFilesConfigTests(unittest.TestCase):
    def test_writes_default_cloud_files_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            setup_tools.install_cloud_files_config(
                home=home,
                remote_llm_root="assistant/",
                dry_run=False,
            )
            config_path = home / ".config" / "cloud-files" / "config.json"
            self.assertTrue(config_path.is_file())
            payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["remote_llm_root"], "assistant/")
        self.assertEqual(payload["timeout_seconds"], 45)

    def test_existing_timeout_is_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_path = home / ".config" / "cloud-files" / "config.json"
            config_path.parent.mkdir(parents=True)
            config_path.write_text(
                json.dumps(
                    {
                        "remote_llm_root": "old-root/",
                        "timeout_seconds": 11,
                    }
                ),
                encoding="utf-8",
            )

            setup_tools.install_cloud_files_config(
                home=home,
                remote_llm_root="assistant/",
                dry_run=False,
            )
            payload = json.loads(config_path.read_text(encoding="utf-8"))

        self.assertEqual(payload["remote_llm_root"], "assistant/")
        self.assertEqual(payload["timeout_seconds"], 11)


class CloudFilesOauthSetupTests(unittest.TestCase):
    def test_client_setup_lines_reference_client_json_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            lines = setup_tools.cloud_files_client_setup_lines(home)

        self.assertTrue(lines)
        rendered = "\n".join(lines)
        self.assertIn("client.json", rendered)
        self.assertIn("Publish app", rendered)

    def test_skips_when_credentials_already_exist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            creds = home / ".config" / "cloud-files" / "credentials.json"
            creds.parent.mkdir(parents=True)
            creds.write_text("{}", encoding="utf-8")

            with mock.patch.object(setup_tools.subprocess, "run") as run:
                status = setup_tools.maybe_run_cloud_files_oauth_setup(
                    home,
                    Path("/repo"),
                    dry_run=False,
                    stdin_isatty=False,
                )

        self.assertEqual(status, "already_configured")
        run.assert_not_called()

    def test_runs_setup_oauth_when_client_json_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            client = home / ".config" / "cloud-files" / "client.json"
            client.parent.mkdir(parents=True)
            client.write_text("{}", encoding="utf-8")

            with mock.patch.object(
                setup_tools.subprocess,
                "run",
                return_value=mock.Mock(returncode=0),
            ) as run:
                status = setup_tools.maybe_run_cloud_files_oauth_setup(
                    home,
                    Path("/repo"),
                    dry_run=False,
                    stdin_isatty=False,
                )

        self.assertEqual(status, "configured")
        run.assert_called_once_with(
            [sys.executable, "/repo/scripts/invoke_skill_export.py", "--caller-skill", "install-assistant-tools", "cloud-files", "setup-oauth"],
            check=False,
        )


    def test_g_calendar_setup_lines_reference_client_json_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            lines = setup_tools.g_calendar_client_setup_lines(home)

        self.assertTrue(lines)
        rendered = "\n".join(lines)
        self.assertIn("client.json", rendered)
        self.assertIn("Publish app", rendered)

    def test_runs_g_calendar_setup_oauth_when_client_json_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            client = home / ".config" / "g-calendar" / "client.json"
            client.parent.mkdir(parents=True)
            client.write_text("{}", encoding="utf-8")

            with mock.patch.object(
                setup_tools.subprocess,
                "run",
                return_value=mock.Mock(returncode=0),
            ) as run:
                status = setup_tools.maybe_run_g_calendar_oauth_setup(
                    home,
                    Path("/repo"),
                    dry_run=False,
                    stdin_isatty=False,
                )

        self.assertEqual(status, "configured")
        run.assert_called_once_with(
            [sys.executable, "/repo/scripts/invoke_skill_export.py", "--caller-skill", "install-assistant-tools", "g-calendar", "setup-oauth"],
            check=False,
        )

    def test_optional_google_services_prompt_runs_selected_setups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for skill in ("cloud-files", "g-calendar"):
                client = home / ".config" / skill / "client.json"
                client.parent.mkdir(parents=True, exist_ok=True)
                client.write_text("{}", encoding="utf-8")

            with mock.patch.object(
                setup_tools.subprocess,
                "run",
                return_value=mock.Mock(returncode=0),
            ) as run:
                statuses = setup_tools.maybe_run_optional_google_oauth_setups(
                    home,
                    Path("/repo"),
                    dry_run=False,
                    stdin_isatty=True,
                    input_func=lambda _prompt: "b",
                )

        self.assertEqual(statuses["cloud-files"], "configured")
        self.assertEqual(statuses["g-calendar"], "configured")
        self.assertEqual(
            run.call_args_list,
            [
                mock.call([sys.executable, "/repo/scripts/invoke_skill_export.py", "--caller-skill", "install-assistant-tools", "cloud-files", "setup-oauth"], check=False),
                mock.call([sys.executable, "/repo/scripts/invoke_skill_export.py", "--caller-skill", "install-assistant-tools", "g-calendar", "setup-oauth"], check=False),
            ],
        )

    def test_optional_google_services_noninteractive_skips_pending_services(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            for skill in ("cloud-files", "g-calendar"):
                client = home / ".config" / skill / "client.json"
                client.parent.mkdir(parents=True, exist_ok=True)
                client.write_text("{}", encoding="utf-8")

            with mock.patch.object(setup_tools.subprocess, "run") as run:
                statuses = setup_tools.maybe_run_optional_google_oauth_setups(
                    home,
                    Path("/repo"),
                    dry_run=False,
                    stdin_isatty=False,
                )

        self.assertEqual(statuses["cloud-files"], "skipped")
        self.assertEqual(statuses["g-calendar"], "skipped")
        run.assert_not_called()

    def test_noninteractive_missing_client_only_prints_instructions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            with mock.patch.object(setup_tools.subprocess, "run") as run:
                status = setup_tools.maybe_run_cloud_files_oauth_setup(
                    home,
                    Path("/repo"),
                    dry_run=False,
                    stdin_isatty=False,
                )

        self.assertEqual(status, "needs_client_json")
        run.assert_not_called()

    def test_interactive_missing_client_can_continue_and_run_setup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            client = home / ".config" / "cloud-files" / "client.json"

            def fake_input(_prompt: str) -> str:
                client.parent.mkdir(parents=True, exist_ok=True)
                client.write_text("{}", encoding="utf-8")
                return ""

            with mock.patch("builtins.input", side_effect=fake_input), mock.patch.object(
                setup_tools.subprocess,
                "run",
                return_value=mock.Mock(returncode=0),
            ) as run:
                status = setup_tools.maybe_run_cloud_files_oauth_setup(
                    home,
                    Path("/repo"),
                    dry_run=False,
                    stdin_isatty=True,
                )

        self.assertEqual(status, "configured")
        run.assert_called_once_with(
            [sys.executable, "/repo/scripts/invoke_skill_export.py", "--caller-skill", "install-assistant-tools", "cloud-files", "setup-oauth"],
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
