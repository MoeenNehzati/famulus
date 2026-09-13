"""Smoke tests for validators/skill/boundaries.py."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re

import pytest

_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "validators" / "skill" / "boundaries.py"
)
_spec = importlib.util.spec_from_file_location("boundaries", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_direct_cross_skill_path_flagged(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    caller.mkdir(parents=True)
    target.mkdir(parents=True)
    (target / "blueprint.yaml").write_text("name: target-skill\n")
    (caller / "blueprint.yaml").write_text("name: caller-skill\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text(
        "import subprocess\n"
        "subprocess.run(['python3', '../target-skill/_rtx/_helper_tool.py'])\n"
    )
    errors = _mod.validate(tmp_path)
    assert any("target-skill" in e for e in errors)


def test_same_skill_path_allowed(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    skill = skills / "my-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: my-skill\n")
    script = skill / "_rtx" / "run.py"
    script.write_text("import subprocess\nsubprocess.run(['python3', './helper.py'])\n")
    assert _mod.validate(tmp_path) == []


def test_direct_cross_skill_cx_path_flagged(tmp_path: Path) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    (caller / "_cx").mkdir(parents=True)
    target.mkdir(parents=True)
    (caller / "blueprint.yaml").write_text("name: caller-skill\n")
    (target / "blueprint.yaml").write_text("name: target-skill\n")
    (caller / "_cx" / "run-task").write_text(
        "exec ../target-skill/_cx/private-command\n"
    )

    errors = _mod.validate(tmp_path)

    assert any("target-skill" in error for error in errors)


def test_multiple_direct_paths_report_alphabetically_first_skill(
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    alpha = skills / "alpha-skill"
    zeta = skills / "zeta-skill"
    for skill in (caller, alpha, zeta):
        skill.mkdir(parents=True)
        (skill / "blueprint.yaml").write_text(f"name: {skill.name}\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text(
        "use ../zeta-skill/_rtx/run.py and ../alpha-skill/_rtx/run.py\n"
    )

    assert _mod.validate(tmp_path) == [
        "skills/caller-skill/_rtx/run.py:1: direct cross-skill runtime path "
        "to alpha-skill is forbidden"
    ]


def test_sys_path_violation_keeps_per_skill_order_before_later_direct_path(
    tmp_path: Path,
) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    alpha = skills / "alpha-skill"
    zeta = skills / "zeta-skill"
    for skill in (caller, alpha, zeta):
        skill.mkdir(parents=True)
        (skill / "blueprint.yaml").write_text(f"name: {skill.name}\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text(
        "sys.path.insert(0, 'skills/alpha-skill'); "
        "use('../zeta-skill/_rtx/run.py')\n"
    )

    assert _mod.validate(tmp_path) == [
        "skills/caller-skill/_rtx/run.py:1: cross-skill sys.path insertion "
        "to alpha-skill is forbidden"
    ]


def test_boundary_matcher_bundle_is_prepared_once_per_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    for skill in (caller, target):
        skill.mkdir(parents=True)
        (skill / "blueprint.yaml").write_text(f"name: {skill.name}\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text("\n".join(f"value_{index} = {index}" for index in range(100)))
    prepare_calls = 0
    real_prepare = _mod._compile_direct_runtime_patterns

    def counted_prepare(names: list[str]) -> tuple[re.Pattern[str], ...]:
        nonlocal prepare_calls
        prepare_calls += 1
        return real_prepare(names)

    monkeypatch.setattr(_mod, "_compile_direct_runtime_patterns", counted_prepare)

    assert _mod.validate(tmp_path) == []
    assert prepare_calls == 1


def test_clean_runtime_lines_skip_direct_path_matchers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    skills = tmp_path / "skills"
    caller = skills / "caller-skill"
    target = skills / "target-skill"
    for skill in (caller, target):
        skill.mkdir(parents=True)
        (skill / "blueprint.yaml").write_text(f"name: {skill.name}\n")
    script = caller / "_rtx" / "run.py"
    script.parent.mkdir()
    script.write_text("\n".join(f"value_{index} = {index}" for index in range(100)))
    real_compile_patterns = _mod._compile_direct_runtime_patterns
    finditer_calls = 0

    class CountingPattern:
        def __init__(self, pattern: re.Pattern[str]) -> None:
            self._pattern = pattern

        def finditer(self, line: str):
            nonlocal finditer_calls
            finditer_calls += 1
            return self._pattern.finditer(line)

    monkeypatch.setattr(
        _mod,
        "_compile_direct_runtime_patterns",
        lambda names: tuple(
            CountingPattern(pattern) for pattern in real_compile_patterns(names)
        ),
    )

    assert _mod.validate(tmp_path) == []
    assert finditer_calls == 0


def test_non_python_gateway_blueprints_skip_yaml_parsing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_root = tmp_path / "skills" / "caller-skill" / "_rtx"
    blueprints = module_root / "blueprints"
    blueprints.mkdir(parents=True)
    (blueprints / "shell.yaml").write_text(
        "gateway:\n  language: Shell\n  path: run.sh\n"
    )
    (blueprints / "python.yaml").write_text(
        "gateway:\n  language: Python\n  path: run.py\n"
    )
    (module_root / "run.py").write_text("value = 1\n")
    safe_load_calls = 0
    real_safe_load = _mod.yaml.safe_load

    def counted_safe_load(source: str):
        nonlocal safe_load_calls
        safe_load_calls += 1
        return real_safe_load(source)

    monkeypatch.setattr(_mod.yaml, "safe_load", counted_safe_load)

    assert _mod.validate(tmp_path) == []
    assert safe_load_calls == 1


def test_escaped_python_gateway_key_and_language_remain_in_scope(
    tmp_path: Path,
) -> None:
    module_root = tmp_path / "skills" / "caller-skill" / "_rtx"
    blueprints = module_root / "blueprints"
    blueprints.mkdir(parents=True)
    (blueprints / "python.yaml").write_text(
        'gateway:\n  "languag\\x65": "Pyth\\x6fn"\n  path: mutating.py\n'
    )
    (module_root / "mutating.py").write_text("sys.path.insert(0, 'src')\n")

    assert _mod.validate(tmp_path) == [
        "skills/caller-skill/_rtx/mutating.py:1: unguarded module-scope "
        "sys.path mutation in a dispatcher-reachable gateway; guard it on "
        "__package__ or remove it"
    ]


def test_only_gateway_with_whitespace_tolerant_sys_path_token_is_ast_parsed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_root = tmp_path / "skills" / "caller-skill" / "_rtx"
    blueprints = module_root / "blueprints"
    blueprints.mkdir(parents=True)
    for name in ("clean", "mutating"):
        (blueprints / f"{name}.yaml").write_text(
            f"gateway:\n  language: Python\n  path: {name}.py\n"
        )
    (module_root / "clean.py").write_text("value = 1\n")
    (module_root / "mutating.py").write_text("sys . path . insert(0, 'src')\n")
    parse_calls = 0
    real_parse = _mod.ast.parse

    def counted_parse(*args, **kwargs):
        nonlocal parse_calls
        parse_calls += 1
        return real_parse(*args, **kwargs)

    monkeypatch.setattr(_mod.ast, "parse", counted_parse)

    assert _mod.validate(tmp_path) == [
        "skills/caller-skill/_rtx/mutating.py:1: unguarded module-scope "
        "sys.path mutation in a dispatcher-reachable gateway; guard it on "
        "__package__ or remove it"
    ]
    assert parse_calls == 1


def test_gateway_sys_path_line_continuation_remains_in_scope(tmp_path: Path) -> None:
    module_root = tmp_path / "skills" / "caller-skill" / "_rtx"
    blueprints = module_root / "blueprints"
    blueprints.mkdir(parents=True)
    (blueprints / "mutating.yaml").write_text(
        "gateway:\n  language: Python\n  path: mutating.py\n"
    )
    (module_root / "mutating.py").write_text(
        "sys \\\n . path . insert(0, 'src')\n"
    )

    assert _mod.validate(tmp_path) == [
        "skills/caller-skill/_rtx/mutating.py:1: unguarded module-scope "
        "sys.path mutation in a dispatcher-reachable gateway; guard it on "
        "__package__ or remove it"
    ]
