from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from link_utils import make_copy, make_link


def test_make_link_creates_symlink(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    dst = tmp_path / "dst.txt"

    make_link(src, dst, dry_run=False)

    assert dst.is_symlink()
    assert dst.resolve() == src.resolve()


def test_make_link_skips_missing_source(tmp_path, capsys):
    src = tmp_path / "missing.txt"
    dst = tmp_path / "dst.txt"

    make_link(src, dst, dry_run=False)

    assert not dst.exists()
    assert "SKIP (missing source)" in capsys.readouterr().out


def test_make_copy_creates_copy(tmp_path):
    src = tmp_path / "src.txt"
    src.write_text("hello")
    dst = tmp_path / "dst.txt"

    make_copy(src, dst, dry_run=False)

    assert dst.read_text() == "hello"


def test_make_copy_preserves_existing_copy(tmp_path, capsys):
    src = tmp_path / "src.txt"
    src.write_text("v2")
    dst = tmp_path / "dst.txt"
    dst.write_text("v1")

    make_copy(src, dst, dry_run=False)

    # Existing file is NOT overwritten - keeps machine-local state
    assert dst.read_text() == "v1"
    assert "SKIP (exists, keeping machine-local state)" in capsys.readouterr().out
