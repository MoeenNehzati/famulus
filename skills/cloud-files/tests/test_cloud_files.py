from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import cloud_files  # noqa: E402


class CloudFilesTests(unittest.TestCase):
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
            local_path.write_text("todo\n", encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
