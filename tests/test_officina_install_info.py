from __future__ import annotations

from pathlib import Path

import pytest

from officina.install.install_info import InstallInfoError, load_install_info


def test_load_install_info_parses_pinned_versions() -> None:
    info = load_install_info(Path("."))
    assert info.schema_version == 1
    assert info.uv_version == "0.11.29"
    assert info.managed_python == "3.11.15"
    assert info.managed_python_supported == "==3.11.15"


def test_load_install_info_rejects_unknown_schema_version(tmp_path: Path) -> None:
    (tmp_path / "install-info.toml").write_text("schema_version = 99\n")
    with pytest.raises(InstallInfoError):
        load_install_info(tmp_path)


def test_load_install_info_rejects_non_exact_managed_python(tmp_path: Path) -> None:
    (tmp_path / "install-info.toml").write_text(
        """\
schema_version = 1
[bootstrap]
uv_version = "0.11.29"
[managed_python]
preferred = "3.11"
supported = ">=3.11,<3.12"
""",
        encoding="utf-8",
    )

    with pytest.raises(InstallInfoError, match="exact managed Python patch"):
        load_install_info(tmp_path)
