from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"

STUB_CLOUD_FILES = r'''
from __future__ import annotations
import os
import sys
from pathlib import Path

STORE = Path(os.environ["TEST_STORE"])


def _path(relpath: str) -> Path:
    return STORE / relpath


def read_entrypoint(args, *, use_llm_root: bool) -> int:
    if args and args[0] == "--list":
        rel = args[1] if len(args) > 1 else ""
        target = _path(rel)
        if target.is_dir():
            for child in sorted(target.iterdir()):
                name = child.name + ("/" if child.is_dir() else "")
                print(name)
        return 0
    rel = args[0]
    target = _path(rel)
    if not target.exists():
        raise FileNotFoundError(rel)
    sys.stdout.write(target.read_text(encoding="utf-8"))
    return 0


def write_entrypoint(args, *, use_llm_root: bool) -> int:
    rel = args[0]
    target = _path(rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(sys.stdin.read(), encoding="utf-8")
    return 0


def run_entrypoint(entrypoint, args, *, use_llm_root: bool) -> int:
    try:
        return entrypoint(args, use_llm_root=use_llm_root)
    except (FileNotFoundError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


def delete_entrypoint(args, *, use_llm_root: bool) -> int:
    rel = args[0]
    target = _path(rel)
    if target.exists():
        target.unlink()
    return 0
'''


class ScriptEntryPointTests(unittest.TestCase):
    def test_llm_wrappers_round_trip_via_script_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            store = tmpdir / "store"
            store.mkdir()

            for name in ("read_llm_file.py", "write_llm_file.py", "delete_llm_file.py"):
                shutil.copy2(REPO_SCRIPTS / name, tmpdir / name)

            (tmpdir / "cloud_files.py").write_text(STUB_CLOUD_FILES, encoding="utf-8")

            env = os.environ.copy()
            env["TEST_STORE"] = str(store)

            relpath = "scratch/roundtrip.txt"
            content = "script wrapper roundtrip\n"

            write_res = subprocess.run(
                [sys.executable, str(tmpdir / "write_llm_file.py"), relpath],
                input=content,
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(write_res.returncode, 0, write_res.stderr)

            read_res = subprocess.run(
                [sys.executable, str(tmpdir / "read_llm_file.py"), relpath],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(read_res.returncode, 0, read_res.stderr)
            self.assertEqual(read_res.stdout, content)

            list_before_delete = subprocess.run(
                [sys.executable, str(tmpdir / "read_llm_file.py"), "--list", "scratch"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(list_before_delete.returncode, 0, list_before_delete.stderr)
            self.assertIn("roundtrip.txt", list_before_delete.stdout.splitlines())

            delete_res = subprocess.run(
                [sys.executable, str(tmpdir / "delete_llm_file.py"), relpath],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(delete_res.returncode, 0, delete_res.stderr)

            list_after_delete = subprocess.run(
                [sys.executable, str(tmpdir / "read_llm_file.py"), "--list", "scratch"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(list_after_delete.returncode, 0, list_after_delete.stderr)
            self.assertNotIn("roundtrip.txt", list_after_delete.stdout.splitlines())

            missing_read = subprocess.run(
                [sys.executable, str(tmpdir / "read_llm_file.py"), relpath],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            self.assertEqual(missing_read.returncode, 1)
            self.assertEqual(missing_read.stderr.strip(), relpath)
            self.assertNotIn("Traceback", missing_read.stderr)


if __name__ == "__main__":
    unittest.main()
