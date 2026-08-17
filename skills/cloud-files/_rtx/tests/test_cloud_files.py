from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from officina.credentials.google import SERVICE_SCOPES

SCRIPT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPT_DIR))
if __package__ and __package__.count('.') >= 1:
    from .. import _drive_gateway as cloud_files
else:
    import _drive_gateway as cloud_files  # noqa: E402


class CloudFilesTests(unittest.TestCase):
    def setUp(self) -> None:
        # The access-token cache is module-level, so it would otherwise carry
        # between tests and let one test's token satisfy another's call.
        cloud_files._token_cache.clear()

    def test_normalize_llm_root_adds_trailing_slash(self) -> None:
        self.assertEqual(cloud_files.normalize_llm_root("assistant"), "assistant/")

    def test_normalize_llm_root_rejects_parent_segments(self) -> None:
        with self.assertRaises(ValueError):
            cloud_files.normalize_llm_root("../assistant")

    def test_parse_llm_spec_rejects_parent_escape(self) -> None:
        with self.assertRaises(ValueError):
            cloud_files.parse_llm_spec("llm:../../outside.txt")

    def test_read_uses_configured_llm_root(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with mock.patch.object(cloud_files, "load_config", return_value=config):
            with mock.patch.object(cloud_files, "read_text", return_value="hello\n") as read_text:
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = cloud_files.main(["read", "notes/todo.md"])
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue(), "hello\n")
        read_text.assert_called_once_with(config, "notes/todo.md", use_llm_root=True)

    def test_read_missing_llm_root_does_not_attempt_creation(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with mock.patch.object(cloud_files, "list_children", return_value=[]):
            with mock.patch.object(
                cloud_files,
                "create_folder",
                side_effect=AssertionError("read attempted to create a remote folder"),
            ):
                with self.assertRaises(FileNotFoundError):
                    cloud_files.read_text(
                        config,
                        "notes/todo.md",
                        use_llm_root=True,
                    )

    def test_write_reads_stdin_and_targets_llm_root(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with mock.patch.object(cloud_files, "load_config", return_value=config):
            with mock.patch.object(cloud_files, "write_text") as write_text:
                stdin = io.StringIO("new contents")
                with mock.patch("sys.stdin", stdin):
                    rc = cloud_files.main(["write", "plans/today.md"])
        self.assertEqual(rc, 0)
        write_text.assert_called_once_with(
            config, "plans/today.md", "new contents", use_llm_root=True
        )

    def test_delete_targets_llm_root(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with mock.patch.object(cloud_files, "load_config", return_value=config):
            with mock.patch.object(cloud_files, "delete_file") as delete_file:
                rc = cloud_files.main(["delete", "lists/tasks.md"])
        self.assertEqual(rc, 0)
        delete_file.assert_called_once_with(config, "lists/tasks.md", use_llm_root=True)

    def test_read_remote_bypasses_llm_root(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with mock.patch.object(cloud_files, "load_config", return_value=config):
            with mock.patch.object(cloud_files, "read_text", return_value="root-file\n") as read_text:
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = cloud_files.main(["read-remote", "archive/raw.txt"])
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue(), "root-file\n")
        read_text.assert_called_once_with(config, "archive/raw.txt", use_llm_root=False)

    def test_list_prints_one_entry_per_line(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with mock.patch.object(cloud_files, "load_config", return_value=config):
            with mock.patch.object(
                cloud_files, "list_entries", return_value=["a.md", "nested/"]
            ) as list_entries:
                stdout = io.StringIO()
                with mock.patch("sys.stdout", stdout):
                    rc = cloud_files.main(["list", "lists"])
        self.assertEqual(rc, 0)
        self.assertEqual(stdout.getvalue(), "a.md\nnested/\n")
        list_entries.assert_called_once_with(config, "lists", use_llm_root=True)

    def test_cp_download_writes_local_file(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "todo.md"
            with mock.patch.object(cloud_files, "load_config", return_value=config):
                with mock.patch.object(
                    cloud_files,
                    "expand_remote_sources",
                    return_value=[
                        cloud_files.RemoteEntry(
                            path="lists/todo.md",
                            id="abc123",
                            is_dir=False,
                        )
                    ],
                ):
                    with mock.patch.object(
                        cloud_files, "download_bytes", return_value=b"todo\n"
                    ) as download_bytes:
                        rc = cloud_files.main(["cp", "llm:lists/todo.md", str(local_path)])
            local_bytes = local_path.read_bytes()
        self.assertEqual(rc, 0)
        self.assertEqual(local_bytes, b"todo\n")
        download_bytes.assert_called_once_with(
            config,
            "lists/todo.md",
            use_llm_root=True,
        )

    def test_cp_upload_reads_local_file(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            local_path = Path(tmp) / "todo.md"
            local_path.write_bytes(b"todo\n")
            with mock.patch.object(cloud_files, "load_config", return_value=config):
                with mock.patch.object(
                    cloud_files, "resolve_remote_target", return_value="lists/todo.md"
                ):
                    with mock.patch.object(cloud_files, "upload_bytes") as upload_bytes:
                        rc = cloud_files.main(["cp", str(local_path), "llm:lists/todo.md"])
        self.assertEqual(rc, 0)
        upload_bytes.assert_called_once_with(
            config,
            "lists/todo.md",
            b"todo\n",
            source_name="todo.md",
            use_llm_root=True,
        )

    def test_load_config_reads_default_location(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "cloud-files"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "remote_llm_root": "assistant/",
                        "timeout_seconds": 12,
                    }
                ),
                encoding="utf-8",
            )
            config = cloud_files.load_config(home)
        self.assertEqual(config.remote_llm_root, "assistant/")
        self.assertEqual(config.timeout_seconds, 12)
        self.assertEqual(
            config.credentials_path,
            home / ".config" / "cloud-files" / "credentials.json",
        )

    def test_load_config_reads_credential_id_and_home_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "cloud-files"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps(
                    {
                        "remote_llm_root": "assistant/",
                        "timeout_seconds": 12,
                        "credential_id": "google:sub1",
                    }
                ),
                encoding="utf-8",
            )
            config = cloud_files.load_config(home)
        self.assertEqual(config.credential_id, "google:sub1")
        self.assertEqual(config.home, home)

    def test_load_config_credential_id_defaults_to_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            config_dir = home / ".config" / "cloud-files"
            config_dir.mkdir(parents=True)
            (config_dir / "config.json").write_text(
                json.dumps({"remote_llm_root": "assistant/", "timeout_seconds": 12}),
                encoding="utf-8",
            )
            config = cloud_files.load_config(home)
        self.assertIsNone(config.credential_id)

    def test_get_access_token_uses_shared_credential_when_present(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
            credential_id="google:sub1",
            home=Path("/tmp/home"),
        )
        calls = []

        def fake_refresh_access_token(credential_id, **kwargs):
            calls.append((credential_id, kwargs))
            return "fake-access-token"

        with mock.patch(
            "officina.credentials.google.refresh_access_token",
            fake_refresh_access_token,
        ):
            token = cloud_files.get_access_token(config, platform="linux")

        self.assertEqual(token, "fake-access-token")
        self.assertEqual(calls[0][0], "google:sub1")
        from officina.credentials.google import SERVICE_SCOPES

        self.assertEqual(calls[0][1]["required_scopes"], SERVICE_SCOPES["drive"])
        self.assertEqual(calls[0][1]["home"], Path("/tmp/home"))
        self.assertEqual(calls[0][1]["platform"], "linux")

    def test_get_access_token_falls_back_to_legacy_credentials_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            creds_path = Path(tmp) / "credentials.json"
            creds_path.write_text(
                json.dumps(
                    {
                        "client_id": "cid",
                        "client_secret": "csecret",
                        "refresh_token": "rtoken",
                    }
                ),
                encoding="utf-8",
            )
            config = cloud_files.CloudFilesConfig(
                remote_llm_root="assistant/",
                timeout_seconds=45,
                credentials_path=creds_path,
            )
            self.assertIsNone(config.credential_id)

            class FakeResponse:
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *exc):
                    return False

                def read(self_inner):
                    return json.dumps({"access_token": "legacy-token"}).encode("utf-8")

            with mock.patch.object(
                cloud_files.urllib.request, "urlopen", return_value=FakeResponse()
            ) as urlopen:
                token = cloud_files.get_access_token(config)

        self.assertEqual(token, "legacy-token")
        urlopen.assert_called_once()

    def test_access_token_is_reused_within_a_process(self) -> None:
        """drive_request refreshes on every Drive call, so one list write used
        to mint 4-5 tokens and a full triage run around a thousand."""
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
            credential_id="google:cache-me",
            home=Path("/tmp/home"),
        )
        calls = []

        def fake_refresh_access_token(credential_id, **kwargs):
            calls.append(credential_id)
            return "cached-token"

        with mock.patch(
            "officina.credentials.google.refresh_access_token",
            fake_refresh_access_token,
        ):
            first = cloud_files.get_access_token(config, platform="linux")
            second = cloud_files.get_access_token(config, platform="linux")
            third = cloud_files.get_access_token(config, platform="linux")

        self.assertEqual([first, second, third], ["cached-token"] * 3)
        self.assertEqual(len(calls), 1, "token should be minted once, not per call")

    def test_expired_cache_entry_is_refreshed(self) -> None:
        config = cloud_files.CloudFilesConfig(
            remote_llm_root="assistant/",
            timeout_seconds=45,
            credentials_path=Path("/tmp/creds.json"),
            credential_id="google:expiry",
            home=Path("/tmp/home"),
        )
        calls = []

        def fake_refresh_access_token(credential_id, **kwargs):
            calls.append(credential_id)
            return f"token-{len(calls)}"

        with mock.patch(
            "officina.credentials.google.refresh_access_token",
            fake_refresh_access_token,
        ):
            first = cloud_files.get_access_token(config, platform="linux")
            # Force the entry stale rather than sleeping out the real TTL.
            token, _ = cloud_files._token_cache[
                ("id", "google:expiry", frozenset(SERVICE_SCOPES["drive"]))
            ]
            cloud_files._token_cache[
                ("id", "google:expiry", frozenset(SERVICE_SCOPES["drive"]))
            ] = (token, 0.0)
            second = cloud_files.get_access_token(config, platform="linux")

        self.assertEqual([first, second], ["token-1", "token-2"])

    def test_token_error_body_reaches_the_caller(self) -> None:
        """A bare "HTTP 400" is the same string for a revoked grant, a rate
        limit, and a bad client. A scheduled run once read one and reported
        that authentication needed repair, when nothing was wrong with it."""
        from officina.credentials.google import GoogleCredentialError

        error = urllib.error.HTTPError(
            url="https://oauth2.googleapis.com/token",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(
                json.dumps(
                    {
                        "error": "invalid_grant",
                        "error_description": "Token has been expired or revoked.",
                    }
                ).encode("utf-8")
            ),
        )

        with mock.patch(
            "officina.credentials.google._default_urlopen", side_effect=error
        ):
            with self.assertRaises(GoogleCredentialError) as caught:
                cloud_files._diagnostic_urlopen(object(), timeout=5)

        message = str(caught.exception)
        self.assertIn("400", message)
        self.assertIn("invalid_grant", message)
        self.assertIn("Token has been expired or revoked.", message)

    def test_token_error_without_a_usable_body_says_so(self) -> None:
        from officina.credentials.google import GoogleCredentialError

        error = urllib.error.HTTPError(
            url="https://oauth2.googleapis.com/token",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(b"<html>not json</html>"),
        )

        with mock.patch(
            "officina.credentials.google._default_urlopen", side_effect=error
        ):
            with self.assertRaises(GoogleCredentialError) as caught:
                cloud_files._diagnostic_urlopen(object(), timeout=5)

        self.assertIn("429", str(caught.exception))
        self.assertIn("no error detail", str(caught.exception))

    def test_diagnostic_opener_keeps_redirect_rejection(self) -> None:
        """The default opener installs _RejectRedirects so a secret-bearing
        token request never follows a redirect. Wrapping it must not drop
        that, which a bare urllib.request.urlopen would."""
        sentinel = object()
        with mock.patch(
            "officina.credentials.google._default_urlopen", return_value=sentinel
        ) as default_urlopen:
            result = cloud_files._diagnostic_urlopen(object(), timeout=7)

        self.assertIs(result, sentinel)
        default_urlopen.assert_called_once()
        self.assertEqual(default_urlopen.call_args.kwargs["timeout"], 7)


if __name__ == "__main__":
    unittest.main()
