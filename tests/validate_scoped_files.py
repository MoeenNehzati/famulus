"""Whole-file subject selection for repository Python validators."""

from importlib import import_module
from pathlib import Path

import pytest

from officina.common.python_source_cache import PythonSourceCache


@pytest.fixture(params=[
    ("undefined_names", "src/selected.py", "value = missing_name\n"),
    ("toml_io_boundary", "src/selected.py", "value = 'config.toml'\n"),
    ("portable_dates", "src/selected.py", "value = now.strftime('%-d')\n"),
    ("subprocess_text_encoding", "src/selected.py", "import subprocess\nsubprocess.run([], text=True)\n"),
    ("skip_hygiene", "tests/selected.py", "import pytest\npytest.skip('no')\n"),
])
def scoped_validator(request):
    name, relative, source = request.param
    return import_module(f"validators.{name}"), relative, source


def _run_scoped(module, root: Path, paths: tuple[str, ...]) -> list[str]:
    arguments = {"repo_root": root, "validation_paths": paths}
    name = module.__name__.rsplit(".", 1)[-1]
    if name != "undefined_names":
        arguments["python_source_cache"] = PythonSourceCache(root)
    return getattr(module, f"test_{name}")(**arguments)


def test_scope_selects_whole_files_before_reading_or_traversal(
    tmp_path: Path, monkeypatch, scoped_validator,
) -> None:
    module, relative, source = scoped_validator
    selected = tmp_path / relative
    selected.parent.mkdir(parents=True)
    selected.write_text(source, encoding="utf-8")
    selected.with_name("unrelated.py").write_text(source, encoding="utf-8")
    expected = [finding for finding in module.validate(tmp_path) if finding.startswith(relative + ":")]
    assert expected
    excluded = "skills/__pycache__/ignored.py"
    original_read_parse = PythonSourceCache.read_parse

    def read_selected(cache, path):
        assert path == selected, "unselected files must not be read or parsed"
        return original_read_parse(cache, path)

    def no_traversal(*_args, **_kwargs):
        pytest.fail("scoped file validation must not traverse the repository")

    monkeypatch.setattr(PythonSourceCache, "read_parse", read_selected)
    monkeypatch.setattr(Path, "rglob", no_traversal)
    assert _run_scoped(module, tmp_path, (relative, excluded, "outside.py")) == expected
    assert _run_scoped(module, tmp_path, ()) == []


@pytest.mark.parametrize("content, error", [
    (b"if:\n", SyntaxError), (b"\xff", UnicodeDecodeError), (None, FileNotFoundError),
])
def test_scoped_read_and_parse_errors_cannot_pass(
    tmp_path: Path, scoped_validator, content, error,
) -> None:
    module, relative, _source = scoped_validator
    path = tmp_path / relative
    if content is not None:
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
    if error is SyntaxError and not module.__name__.endswith("undefined_names"):
        assert _run_scoped(module, tmp_path, (relative,)) == [
            f"{relative}:1: failed to parse Python: invalid syntax",
        ]
    else:
        with pytest.raises(error):
            _run_scoped(module, tmp_path, (relative,))
