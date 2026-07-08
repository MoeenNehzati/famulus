"""Tests for validators/personal_info.py."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from validators.personal_info import validate  # noqa: E402


def test_empty_repo_passes(tmp_path: Path) -> None:
    assert validate(tmp_path) == []


def test_clean_file_passes(tmp_path: Path) -> None:
    (tmp_path / "notes.md").write_text("Nothing personal here.\n")
    assert validate(tmp_path) == []


def test_token_in_content_detected(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("Contact Moeen for details.\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "doc.md:1" in errors[0]


def test_case_insensitive_and_substring(tmp_path: Path) -> None:
    (tmp_path / "a.txt").write_text("email: smNEHZATI@example.com\n")
    errors = validate(tmp_path)
    assert len(errors) == 1


def test_home_path_detected(tmp_path: Path) -> None:
    (tmp_path / "conf.toml").write_text('root = "/home/moeen/Documents"\n')
    assert len(validate(tmp_path)) == 1


def test_token_in_filename_detected(tmp_path: Path) -> None:
    (tmp_path / "seyed-notes.md").write_text("clean content\n")
    errors = validate(tmp_path)
    assert len(errors) == 1
    assert "file path" in errors[0]


def test_multiple_lines_all_reported(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("moeen\nclean\nnehzati\n")
    assert len(validate(tmp_path)) == 2


def test_binary_file_skipped(tmp_path: Path) -> None:
    (tmp_path / "blob.bin").write_bytes(b"\xff\xfe\x00moeen\x00\xff")
    # undecodable content is skipped; filename is still checked (clean here)
    assert validate(tmp_path) == []


def test_github_handle_allowed(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(
        "git clone git@github.com:MoeenNehzati/claude-config.git\n"
    )
    assert validate(tmp_path) == []


def test_github_handle_does_not_mask_other_tokens(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text("MoeenNehzati and also /home/moeen\n")
    assert len(validate(tmp_path)) == 1


def test_public_github_pages_domain_allowed(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(
        "https://moeennehzati.github.io/assets/html/nehzati2026inference.html\n"
    )
    assert validate(tmp_path) == []


def test_public_github_pages_domain_does_not_mask_other_tokens(tmp_path: Path) -> None:
    (tmp_path / "doc.md").write_text(
        "https://moeennehzati.github.io/ and /home/moeen\n"
    )
    assert len(validate(tmp_path)) == 1


def test_validator_excludes_itself(tmp_path: Path) -> None:
    d = tmp_path / "validators"
    d.mkdir()
    (d / "personal_info.py").write_text('_TOKENS = ("seyed", "moeen", "nehzati")\n')
    t = tmp_path / "tests"
    t.mkdir()
    (t / "validate_personal_info.py").write_text("# mentions moeen\n")
    assert validate(tmp_path) == []
