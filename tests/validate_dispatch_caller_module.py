"""Smoke tests for validators/skill/dispatch_caller_module.py."""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from officina.common.blueprint_graph import load_repository_blueprint_graph
from officina.common.python_source_cache import PythonSourceCache
from v5_blueprint_fixtures import copy_v5_fixture_tree


_VALIDATOR = (
    Path(__file__).resolve().parents[1]
    / "validators" / "skill" / "dispatch_caller_module.py"
)
_spec = importlib.util.spec_from_file_location("dispatch_caller_module", _VALIDATOR)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
_REPO_ROOT = Path(__file__).resolve().parents[1]
_V5_SCHEMA_ROOT = _REPO_ROOT / "references" / "blueprint"
_V5_FIXTURE = _REPO_ROOT / "tests" / "fixtures" / "blueprint_v5" / "authorization"


def test_empty_skills_passes(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    assert _mod.validate(tmp_path) == []


def test_injected_cache_preserves_findings_and_ast(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "demo"
    runtime = skill / "_rtx" / "caller.py"
    runtime.parent.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("", encoding="utf-8")
    runtime.write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(target='demo.run')\n",
        encoding="utf-8",
    )
    expected = _mod.validate(tmp_path)
    source_cache = PythonSourceCache(tmp_path)
    _source, tree = source_cache.read_parse(runtime)
    before = ast.dump(tree, include_attributes=True)

    assert _mod._validate(tmp_path, None, source_cache) == expected
    assert ast.dump(tree, include_attributes=True) == before


def test_v5_dispatch_caller_uses_deepest_registered_module(
    tmp_path: Path,
) -> None:
    copy_v5_fixture_tree(_V5_FIXTURE / "modules", tmp_path / "modules")
    copy_v5_fixture_tree(_V5_FIXTURE / "skills", tmp_path / "skills")
    graph = load_repository_blueprint_graph(
        tmp_path,
        schema_root=_V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )
    runtime = tmp_path / "skills" / "demo" / "_rtx" / "runtime.py"
    runtime.write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='demo-rtx', target='leaf.interface.run')\n",
        encoding="utf-8",
    )

    assert _mod.validate_with_graph(tmp_path, graph) == []

    runtime.write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='demo', target='leaf.interface.run')\n",
        encoding="utf-8",
    )
    errors = _mod.validate_with_graph(tmp_path, graph)
    assert any("expected `demo-rtx`" in error for error in errors)


def test_v5_validator_checks_registered_modules_outside_skills(
    tmp_path: Path,
) -> None:
    copy_v5_fixture_tree(_V5_FIXTURE / "modules", tmp_path / "modules")
    copy_v5_fixture_tree(_V5_FIXTURE / "skills", tmp_path / "skills")
    graph = load_repository_blueprint_graph(
        tmp_path,
        schema_root=_V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )
    caller = tmp_path / "modules" / "outsider" / "caller.py"
    caller.write_text(
        "from officina.runtime.python_machine_interface import DispatchCall\n"
        "CALL = DispatchCall(caller_module_id='root', "
        "target_module_id='leaf', interface='run')\n",
        encoding="utf-8",
    )

    errors = _mod.validate_with_graph(tmp_path, graph)

    assert any(
        "modules/outsider/caller.py" in error
        and "expected `outsider`" in error
        for error in errors
    )


def test_registered_child_tests_are_not_runtime_caller_declarations(
    tmp_path: Path,
) -> None:
    skill = tmp_path / "skills" / "demo-skill"
    test_file = skill / "_rtx" / "tests" / "test_dispatch.py"
    test_file.parent.mkdir(parents=True)
    (skill / "blueprint.yaml").write_text(
        "name: demo-skill\n", encoding="utf-8"
    )
    test_file.write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='fixture', target_skill='other', "
        "script_interface='x')\n",
        encoding="utf-8",
    )

    assert _mod.validate(tmp_path) == []


def test_literal_match_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "good-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: good-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='good-skill', target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_module_constant_match_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "good-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: good-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "OWNER = 'good-skill'\n"
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill=OWNER, target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_dispatch_call_literal_match_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "good-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: good-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.runtime.python_machine_interface import DispatchCall\n"
        "DISPATCHES = {\n"
        "    'read': DispatchCall(caller_module_id='good-skill', target_module_id='other', interface='x')\n"
        "}\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_dispatch_call_module_constant_match_passes(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "good-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: good-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "OWNER = 'good-skill'\n"
        "import officina.runtime.python_machine_interface as pmi\n"
        "DISPATCHES = {\n"
        "    'read': pmi.DispatchCall(caller_module_id=OWNER, target_module_id='other', interface='x')\n"
        "}\n",
        encoding="utf-8",
    )
    assert _mod.validate(tmp_path) == []


def test_missing_caller_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("must include caller_skill" in error for error in errors)


def test_dispatch_call_missing_caller_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.runtime.python_machine_interface import DispatchCall\n"
        "DISPATCHES = {'read': DispatchCall(target_skill='other', interface='x')}\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("DispatchCall() must include caller_module_id" in error for error in errors)


def test_v5_dispatch_call_legacy_keywords_flagged(tmp_path: Path) -> None:
    copy_v5_fixture_tree(_V5_FIXTURE / "skills", tmp_path / "skills")
    graph = load_repository_blueprint_graph(
        tmp_path,
        schema_root=_V5_SCHEMA_ROOT,
        expected_schema_version=5,
    )
    runtime = tmp_path / "skills" / "demo" / "_rtx" / "runtime.py"
    runtime.write_text(
        "from officina.runtime.python_machine_interface import DispatchCall\n"
        "DISPATCHES = {\n"
        "    'read': DispatchCall(caller_skill='demo-rtx', target_skill='other', interface='x')\n"
        "}\n",
        encoding="utf-8",
    )

    errors = _mod.validate_with_graph(tmp_path, graph)
    assert any("caller_module_id and target_module_id" in error for error in errors)


def test_wrong_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "dispatch(caller_skill='other-skill', target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("expected `bad-skill`" in error for error in errors)


def test_dispatch_call_wrong_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.runtime.python_machine_interface import DispatchCall\n"
        "DISPATCHES = {\n"
        "    'read': DispatchCall(caller_module_id='other-skill', target_module_id='other', interface='x')\n"
        "}\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("expected `bad-skill`" in error for error in errors)


def test_dynamic_caller_skill_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from officina.dispatcher import dispatch\n"
        "def wrapper(caller_skill: str):\n"
        "    dispatch(caller_skill=caller_skill, target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("module-level string constant" in error for error in errors)


def test_famulus_dispatcher_import_flagged(tmp_path: Path) -> None:
    skill = tmp_path / "skills" / "bad-skill"
    (skill / "_rtx").mkdir(parents=True)
    (skill / "blueprint.yaml").write_text("name: bad-skill\n", encoding="utf-8")
    (skill / "_rtx" / "run.py").write_text(
        "from famulus.dispatcher import dispatch\n"
        "dispatch(caller_skill='bad-skill', target_skill='other', script_interface='x')\n",
        encoding="utf-8",
    )
    errors = _mod.validate(tmp_path)
    assert any("removed famulus.dispatcher" in error for error in errors)
