from __future__ import annotations

import stat
from pathlib import Path

import pytest

from officina.common import codex_toml, toml_io


BEGIN = "# >>> famulus-access >>>"
END = "# <<< famulus-access <<<"


def test_access_plan_preserves_foreign_toml_and_crlf(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    original = (
        'model = "keep"\r\n'
        "[sandbox_workspace_write]\r\n"
        'writable_roots = [\r\n  "/foreign", # keep\r\n]\r\n'
    ).encode()
    config.write_bytes(original)
    config.chmod(0o640)

    plan = codex_toml.plan_access_roots(
        tmp_path,
        ["/foreign", "/famulus/logs"],
        prior=None,
        begin=BEGIN,
        end=END,
    )

    assert config.read_bytes() == original
    assert plan.introduced == ("/famulus/logs",)
    codex_toml.apply_access_plan(plan)

    result = config.read_bytes()
    assert b'model = "keep"\r\n' in result
    assert b'  "/foreign", # keep\r\n' in result
    assert stat.S_IMODE(config.stat().st_mode) == 0o640
    inspection = codex_toml.inspect_access_roots(tmp_path, begin=BEGIN, end=END)
    assert inspection.roots == ("/foreign", "/famulus/logs")
    assert inspection.marker_values == ("/famulus/logs",)
    assert inspection.marker_within_array
    assert inspection.block_sha256 == plan.block_sha256


def test_access_plan_rejects_external_edit_before_replace(tmp_path: Path) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "before"\n', encoding="utf-8")
    plan = codex_toml.plan_access_roots(
        tmp_path, ["/famulus/logs"], prior=None, begin=BEGIN, end=END
    )
    config.write_text('model = "external"\n', encoding="utf-8")

    with pytest.raises(toml_io.TomlManagedArrayError, match="changed before atomic"):
        codex_toml.apply_access_plan(plan)

    assert config.read_text(encoding="utf-8") == 'model = "external"\n'


def test_access_removal_plan_removes_only_owned_block_and_scaffolding(
    tmp_path: Path,
) -> None:
    config = tmp_path / "config.toml"
    config.write_text('model = "keep"\n', encoding="utf-8")
    install = codex_toml.plan_access_roots(
        tmp_path, ["/famulus/logs"], prior=None, begin=BEGIN, end=END
    )
    codex_toml.apply_access_plan(install)
    ownership = {
        "begin": BEGIN,
        "end": END,
        "block_sha256": install.block_sha256,
        "created_key": install.created_key,
        "created_table": install.created_table,
        "created_file": install.created_file,
    }

    removal = codex_toml.plan_access_removal(tmp_path, ownership=ownership)
    codex_toml.apply_access_plan(removal)

    assert config.read_text(encoding="utf-8") == 'model = "keep"\n'
