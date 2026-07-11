"""Tests for validators/skip_hygiene.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path

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
