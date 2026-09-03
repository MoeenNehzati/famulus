"""Tests for validators/skip_hygiene.py."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from officina.common.python_source_cache import PythonSourceCache

_VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "skip_hygiene.py"
_spec = importlib.util.spec_from_file_location("skip_hygiene", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)


def _write_test(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "tests" / "test_demo.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_unannotated_pytest_skip_is_rejected(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "import pytest\n\n"
        "def test_demo():\n"
        "    pytest.skip('not here')\n",
    )

    errors = _mod.validate(tmp_path)

    assert any("test skip must have a nearby" in error for error in errors)


def test_unannotated_module_owned_test_skip_is_rejected(tmp_path: Path) -> None:
    path = (
        tmp_path / "skills" / "llm-wakeup" / "_rtx" / "tests" / "test_demo.py"
    )
    path.parent.mkdir(parents=True)
    path.write_text(
        "import pytest\n\n"
        "def test_demo():\n"
        "    pytest.skip('not here')\n",
        encoding="utf-8",
    )

    errors = _mod.validate(tmp_path)

    assert any("test skip must have a nearby" in error for error in errors)


def test_injected_cache_preserves_findings_and_ast(tmp_path: Path) -> None:
    path = _write_test(tmp_path, "import pytest\npytest.skip('no')\n")
    expected = _mod.validate(tmp_path)
    source_cache = PythonSourceCache(tmp_path)
    _source, tree = source_cache.read_parse(path)
    before = ast.dump(tree, include_attributes=True)

    assert _mod._validate(tmp_path, source_cache) == expected
    assert ast.dump(tree, include_attributes=True) == before


def test_injected_cache_preserves_syntax_error_finding(tmp_path: Path) -> None:
    _write_test(tmp_path, "if:\n")

    assert _mod._validate(
        tmp_path,
        PythonSourceCache(tmp_path),
    ) == _mod.validate(tmp_path)


def test_token_negative_source_uses_injected_cache_without_walking_ast(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = _write_test(tmp_path, "def test_demo():\n    assert True\n")
    source_cache = PythonSourceCache(tmp_path)

    def _skip_lines_must_not_run(_tree: ast.AST) -> list[int]:
        pytest.fail("token-negative source must not walk the AST for skips")

    monkeypatch.setattr(_mod, "_skip_lines", _skip_lines_must_not_run)

    assert _mod._validate(tmp_path, source_cache) == []
    path.unlink()
    source, tree = source_cache.read_parse(path)
    assert source == "def test_demo():\n    assert True\n"
    assert isinstance(tree, ast.Module)


def test_token_negative_syntax_error_is_reported_exactly(tmp_path: Path) -> None:
    _write_test(tmp_path, "if:\n")

    assert _mod._validate(tmp_path, PythonSourceCache(tmp_path)) == [
        "tests/test_demo.py:1: failed to parse Python: invalid syntax"
    ]


@pytest.mark.parametrize(
    ("source", "lineno"),
    [
        ("import pytest\n\ndef test_demo():\n    pytest.skip('no')\n", 4),
        (
            "import pytest\n\n@pytest.mark.skip(reason='no')\ndef test_demo():\n    pass\n",
            3,
        ),
        (
            "import pytest\n\n@pytest.mark.skipif(True, reason='no')\ndef test_demo():\n    pass\n",
            3,
        ),
        (
            "import unittest\n\n@unittest.skip('no')\ndef test_demo():\n    pass\n",
            3,
        ),
        (
            "import unittest\n\n@unittest.skipIf(True, 'no')\ndef test_demo():\n    pass\n",
            3,
        ),
        (
            "import unittest\n\ndef test_demo(case):\n    case.skipTest('no')\n",
            4,
        ),
        (
            "import unittest\n\ndef test_demo():\n    raise unittest.SkipTest('no')\n",
            4,
        ),
        (
            "import pytest\n\ndef test_demo():\n    raise pytest.SkipTest('no')\n",
            4,
        ),
    ],
    ids=(
        "pytest-skip",
        "pytest-mark-skip",
        "pytest-mark-skipif",
        "unittest-skip",
        "unittest-skipif",
        "testcase-skiptest",
        "unittest-skiptest",
        "pytest-skiptest",
    ),
)
def test_recognized_skip_forms_survive_token_gate_with_exact_finding(
    tmp_path: Path,
    source: str,
    lineno: int,
) -> None:
    _write_test(tmp_path, source)

    assert _mod.validate(tmp_path) == [
        f"tests/test_demo.py:{lineno}: test skip must have a nearby "
        "`# famulus-skip: category=...; reason=...; alternate=...` comment"
    ]


def test_injected_cache_preserves_unicode_error(tmp_path: Path) -> None:
    path = tmp_path / "tests" / "test_demo.py"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\xff")

    with pytest.raises(UnicodeDecodeError) as direct:
        _mod.validate(tmp_path)
    with pytest.raises(UnicodeDecodeError) as injected:
        _mod._validate(tmp_path, PythonSourceCache(tmp_path))

    assert direct.value.args == injected.value.args


def test_injected_cache_preserves_os_error(tmp_path: Path, monkeypatch) -> None:
    missing = tmp_path / "tests" / "test_missing.py"
    monkeypatch.setattr(
        _mod,
        "_iter_python_test_files",
        lambda _root: iter([(missing, Path("tests/test_missing.py"))]),
    )

    with pytest.raises(FileNotFoundError) as direct:
        _mod.validate(tmp_path)
    with pytest.raises(FileNotFoundError) as injected:
        _mod._validate(tmp_path, PythonSourceCache(tmp_path))

    assert direct.value.args == injected.value.args


def test_annotated_skipif_decorator_passes(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "import sys\nimport pytest\n\n"
        "# famulus-skip: category=platform-contract; reason=Windows uses registry; "
        "alternate=test_registry_path\n"
        "@pytest.mark.skipif(sys.platform == 'win32', reason='registry')\n"
        "def test_demo():\n"
        "    assert True\n",
    )

    assert _mod.validate(tmp_path) == []


def test_missing_marker_field_is_rejected(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "import pytest\n\n"
        "# famulus-skip: category=platform-contract; reason=Windows uses registry\n"
        "pytestmark = pytest.mark.skipif(True, reason='not this host')\n",
    )

    errors = _mod.validate(tmp_path)

    assert any("missing field(s): alternate" in error for error in errors)


def test_unittest_skiptest_requires_marker(tmp_path: Path) -> None:
    _write_test(
        tmp_path,
        "import unittest\n\n"
        "def test_demo():\n"
        "    raise unittest.SkipTest('not here')\n",
    )

    errors = _mod.validate(tmp_path)

    assert any("test skip must have a nearby" in error for error in errors)
