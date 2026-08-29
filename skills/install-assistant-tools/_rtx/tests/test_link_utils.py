from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if __package__ and __package__.count('.') >= 1:
    from .. import _fs_links
    from .._fs_links import make_copy, make_link
else:
    import _fs_links
    from _fs_links import make_copy, make_link


def _deny_link(*_args, **_kwargs):
    raise PermissionError("blocked")


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


def test_make_link_reports_windows_symlink_guidance(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello")
    monkeypatch.setattr(Path, "symlink_to", _deny_link)
    monkeypatch.setattr(_fs_links.sys, "platform", "win32")

    make_link(src, dst, dry_run=False)

    assert "Developer Mode or administrator privileges" in capsys.readouterr().out


def test_make_link_reports_generic_failure_outside_windows(tmp_path, monkeypatch, capsys):
    src = tmp_path / "src.txt"
    dst = tmp_path / "dst.txt"
    src.write_text("hello")
    monkeypatch.setattr(Path, "symlink_to", _deny_link)
    monkeypatch.setattr(_fs_links.sys, "platform", "linux")

    make_link(src, dst, dry_run=False)

    output = capsys.readouterr().out
    assert "could not create symlink" in output
    assert "Developer Mode" not in output


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
