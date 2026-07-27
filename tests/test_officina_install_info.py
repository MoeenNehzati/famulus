from __future__ import annotations

from pathlib import Path

import pytest

from officina.install.install_info import InstallInfoError, load_install_info


def test_load_install_info_parses_pinned_versions() -> None:
    info = load_install_info(Path("install-info.toml"))
    assert info.schema_version == 1
    assert info.uv_version == "0.11.29"
    assert info.managed_python == "3.11"


def test_load_install_info_rejects_unknown_schema_version(tmp_path: Path) -> None:
    bad = tmp_path / "install-info.toml"
    bad.write_text("schema_version = 99\n")
    with pytest.raises(InstallInfoError):
        load_install_info(bad)
