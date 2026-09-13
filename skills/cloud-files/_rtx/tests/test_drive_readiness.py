from __future__ import annotations

import io, json, sys, unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
if __package__ and __package__.count(".") >= 1:
    from .. import _drive_gateway, _drive_readiness
else:
    import _drive_gateway, _drive_readiness
class EnsureAssistantRootTests(unittest.TestCase):
    def test_calls_resolver_with_create_true(self)->None:
        config=_drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/",timeout_seconds=45,credentials_path=Path("/tmp/creds.json"))
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway, "resolve_base_id", return_value="id"
            ) as resolve_mock:
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = _drive_readiness.ensure_assistant_root()
        self.assertEqual(rc, 0)
        resolve_mock.assert_called_once_with(config, use_llm_root=True, create=True)
        output = stdout.getvalue()
        self.assertIn('"exists": true', output)

    def test_already_existing_root(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway, "resolve_base_id", return_value="id"
            ) as resolve_mock:
                _drive_readiness.ensure_assistant_root()
        resolve_mock.assert_called_once()

    def test_wrong_root_fails(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="other/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            stderr = io.StringIO()
            with mock.patch("sys.stderr", stderr):
                rc = _drive_readiness.ensure_assistant_root()
        self.assertEqual(rc, 1)

    def test_drive_error(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway,
                "resolve_base_id",
                side_effect=_drive_gateway.CloudFilesError("error"),
            ):
                rc = _drive_readiness.ensure_assistant_root()
        self.assertEqual(rc, 1)
class ListsExistsTests(unittest.TestCase):
    def test_file_found(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        entry = _drive_gateway.RemoteEntry(path="lists/todo.yaml", id="id", is_dir=False)
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway, "resolve_base_id", return_value="id"
            ):
                with mock.patch.object(
                    _drive_readiness._drive_gateway, "resolve_entry", return_value=entry
                ):
                    stdout = io.StringIO()
                    with mock.patch("sys.stdout", stdout):
                        rc = _drive_readiness.lists_exists("lists/todo.yaml")
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertTrue(parsed["exists"])

    def test_file_not_found(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway, "resolve_base_id", return_value="id"
            ):
                with mock.patch.object(
                    _drive_readiness._drive_gateway, "resolve_entry", side_effect=FileNotFoundError()
                ):
                    stdout = io.StringIO()
                    with mock.patch("sys.stdout", stdout):
                        rc = _drive_readiness.lists_exists("lists/todo.yaml")
        self.assertEqual(rc, 0)
        parsed = json.loads(stdout.getvalue())
        self.assertFalse(parsed["exists"])

    def test_never_creates(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway, "resolve_base_id", return_value="id"
            ) as resolve_mock:
                with mock.patch.object(
                    _drive_readiness._drive_gateway, "resolve_entry", side_effect=FileNotFoundError()
                ):
                    _drive_readiness.lists_exists("lists/todo.yaml")
        resolve_mock.assert_called_once_with(config, use_llm_root=True, create=False)

    def test_ambiguous_path_error(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway, "resolve_base_id", return_value="id"
            ):
                with mock.patch.object(
                    _drive_readiness._drive_gateway,
                    "resolve_entry",
                    side_effect=_drive_gateway.CloudFilesError("ambiguous"),
                ):
                    rc = _drive_readiness.lists_exists("lists/todo.yaml")
        self.assertEqual(rc, 1)

    def test_auth_error(self) -> None:
        config = _drive_gateway.CloudFilesConfig(
            remote_llm_root="assistant/", timeout_seconds=45, credentials_path=Path("/tmp/creds.json")
        )
        with mock.patch.object(_drive_readiness._drive_gateway, "load_config", return_value=config):
            with mock.patch.object(
                _drive_readiness._drive_gateway,
                "resolve_base_id",
                side_effect=_drive_gateway.CloudFilesError("auth"),
            ):
                rc = _drive_readiness.lists_exists("lists/todo.yaml")
        self.assertEqual(rc, 1)

    def test_invalid_path(self) -> None:
        rc = _drive_readiness.lists_exists("invalid/path")
        self.assertEqual(rc, 1)

    def test_non_lists_prefix(self) -> None:
        rc = _drive_readiness.lists_exists("plans/file.md")
        self.assertEqual(rc, 1)

    def test_empty_after_prefix(self) -> None:
        rc = _drive_readiness.lists_exists("lists/")
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
