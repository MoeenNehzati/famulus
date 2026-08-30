"""Tests for validators/subprocess_text_encoding.py."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from officina.common.python_source_cache import PythonSourceCache

_VALIDATOR = Path(__file__).resolve().parents[1] / "validators" / "subprocess_text_encoding.py"
_spec = importlib.util.spec_from_file_location("subprocess_text_encoding", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def _write_runtime(tmp_path: Path, source: str) -> Path:
    path = tmp_path / "skills" / "demo-skill" / "_rtx" / "_run_tool.py"
    path.parent.mkdir(parents=True)
    path.write_text(source, encoding="utf-8")
    return path


def test_text_encoding_policy_matrix_reports_exact_findings_in_source_order(
    tmp_path: Path,
) -> None:
    _write_runtime(
        tmp_path,
        "import subprocess\n"
        "subprocess.run(['tool'], capture_output=True, check=False)\n"
        "subprocess.run(['tool'], text=True)\n"
        "subprocess.Popen(['tool'], universal_newlines=True)\n"
        "subprocess.call(['tool'], encoding='utf-8')\n"
        "subprocess.check_call(['tool'], errors='strict')\n"
        "subprocess.check_output(['tool'], encoding='utf-8', errors='strict')\n",
    )

    assert _mod.validate(tmp_path) == [
        "skills/demo-skill/_rtx/_run_tool.py:3: subprocess text mode must set "
        "both encoding and errors explicitly",
        "skills/demo-skill/_rtx/_run_tool.py:4: subprocess text mode must set "
        "both encoding and errors explicitly",
        "skills/demo-skill/_rtx/_run_tool.py:5: subprocess text mode must set "
        "both encoding and errors explicitly",
        "skills/demo-skill/_rtx/_run_tool.py:6: subprocess text mode must set "
        "both encoding and errors explicitly",
    ]


def test_injected_cache_preserves_findings_and_ast(tmp_path: Path) -> None:
    path = _write_runtime(
        tmp_path,
        "import subprocess\nsubprocess.run(['tool'], text=True)\n",
    )
    expected = _mod.validate(tmp_path)
    source_cache = PythonSourceCache(tmp_path)
    _source, tree = source_cache.read_parse(path)
    before = ast.dump(tree, include_attributes=True)

    assert _mod._validate(tmp_path, source_cache) == expected
    assert ast.dump(tree, include_attributes=True) == before


def test_excluded_tests_and_system_skill_trees_are_ignored(tmp_path: Path) -> None:
    for path in (
        tmp_path / "skills" / "demo-skill" / "tests" / "test_tool.py",
        tmp_path / "skills" / ".system" / "skill-installer" / "scripts" / "installer.py",
    ):
        path.parent.mkdir(parents=True)
        path.write_text(
            "import subprocess\n"
            "subprocess.run(['tool'], capture_output=True, text=True)\n",
            encoding="utf-8",
        )

    assert _mod.validate(tmp_path) == []


def test_token_negative_source_avoids_ast_walk(tmp_path: Path, monkeypatch) -> None:
    path = _write_runtime(tmp_path, "value = 1\n")
    source_cache = PythonSourceCache(tmp_path)
    original_ast_walk = _mod.ast.walk
    walked_trees: list[ast.AST] = []

    def record_ast_walk(tree):
        walked_trees.append(tree)
        return original_ast_walk(tree)

    monkeypatch.setattr(_mod.ast, "walk", record_ast_walk)

    assert _mod._validate_python(
        path,
        path.relative_to(tmp_path),
        source_cache,
    ) == []
    assert walked_trees == []


def test_normalized_unicode_subprocess_identifier_reports_exact_diagnostic(
    tmp_path: Path,
) -> None:
    path = _write_runtime(
        tmp_path,
        "import ｓｕｂｐｒｏｃｅｓｓ\n"
        "ｓｕｂｐｒｏｃｅｓｓ.run(['tool'], text=True)\n",
    )
    source_cache = PythonSourceCache(tmp_path)

    assert _mod._validate_python(
        path,
        path.relative_to(tmp_path),
        source_cache,
    ) == [
        "skills/demo-skill/_rtx/_run_tool.py:2: subprocess text mode must set "
        "both encoding and errors explicitly"
    ]


def test_malformed_token_negative_source_reports_parse_diagnostic(tmp_path: Path) -> None:
    path = _write_runtime(tmp_path, "value =\n")
    source_cache = PythonSourceCache(tmp_path)

    assert _mod._validate_python(
        path,
        path.relative_to(tmp_path),
        source_cache,
    ) == [
        "skills/demo-skill/_rtx/_run_tool.py:1: failed to parse Python: invalid syntax"
    ]
