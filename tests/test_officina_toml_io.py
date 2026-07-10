from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.common import toml_io  # noqa: E402


def test_open_writes_utf8_and_validates_toml_on_close(tmp_path: Path) -> None:
    with toml_io.open(tmp_path, "config.toml", "w") as f:
        f.write('name = "cafe"\n')

    with toml_io.open(tmp_path, "config.toml", "r") as f:
        assert f.read() == 'name = "cafe"\n'


def test_open_rejects_invalid_toml_after_write(tmp_path: Path) -> None:
    with pytest.raises(tomllib.TOMLDecodeError):
        with toml_io.open(tmp_path, "config.toml", "w") as f:
            f.write('path = "C:\\Users\\tester"\n')


def test_key_value_round_trips_windows_path(tmp_path: Path) -> None:
    value = r"C:\Users\tester\Famulus\agents\assistant.md"

    with toml_io.open(tmp_path, "config.toml", "w") as f:
        f.write(toml_io.key_value("model_instructions_file", value))

    parsed = tomllib.loads((tmp_path / "config.toml").read_text(encoding="utf-8"))
    assert parsed["model_instructions_file"] == value


@pytest.mark.parametrize("name", ["config.json", "../config.toml", "dir/config.toml"])
def test_open_rejects_non_toml_or_path_names(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError):
        toml_io.open(tmp_path, name, "r")


def test_open_rejects_binary_mode(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        toml_io.open(tmp_path, "config.toml", "rb")
