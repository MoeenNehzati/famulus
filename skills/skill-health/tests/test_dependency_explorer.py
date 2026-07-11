from __future__ import annotations

import importlib.util
import sys
from pathlib import Path



MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_get_health_state.py"
SPEC = importlib.util.spec_from_file_location("skill_get_health_state", MODULE_PATH)
health_state = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = health_state
SPEC.loader.exec_module(health_state)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def assert_label_suffix(labels: set[str], suffix: str) -> None:
    assert any(label.endswith(suffix) for label in labels), suffix


def test_explore_file_recurses_markdown_references_from_base_dir(tmp_path: Path) -> None:
    repo = tmp_path
    write(repo / "root.md", "See @alpha.beta.md, dir/slash.md, win\\path.txt, and missing.md.\n")
    write(repo / "alpha.beta.md", "Then read second.md.\n")
    write(repo / "second.md", "done\n")
    write(repo / "dir" / "slash.md", "slash\n")
    write(repo / "win" / "path.txt", "windows path\n")

    labels = {
        item.label
        for item in health_state.DependencyExplorer(repo).explore_file(repo / "root.md", base_dir=repo)
    }

    assert labels == {
        "root.md",
        "alpha.beta.md",
        "second.md",
        "dir/slash.md",
        "win/path.txt",
    }


def test_explore_interface_unions_direct_roots_and_python_dependencies(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "NOTES.md", "See references/extra.md.\n")
    write(skill / "references" / "extra.md", "extra\n")
    write(skill / "_rtx" / "__init__.py", "")
    write(skill / "_rtx" / "_helper.py", "VALUE = 'ok'\n")
    write(
        skill / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "from . import _helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        assert _helper.VALUE",
                "",
            ]
        ),
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": ["NOTES.md"],
        "directly_executes": ["_rtx/_main.py"],
        "directly_writes": [],
    }

    labels = {
        item.label
        for item in health_state.DependencyExplorer(repo).explore_interface(skill, spec)
    }

    assert {
        "skills/demo-skill/NOTES.md",
        "skills/demo-skill/references/extra.md",
        "skills/demo-skill/_rtx/__init__.py",
        "skills/demo-skill/_rtx/_main.py",
        "skills/demo-skill/_rtx/_helper.py",
    } <= labels


def test_explore_skill_unions_interfaces_and_compatibility_files(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "See guide.md.\n")
    write(skill / "guide.md", "guide\n")
    write(skill / "_rtx" / "_worker.py", "print('worker')\n")
    write(skill / "depends_on_skills", "none\n")
    write(skill / "permissions.json", "{}\n")
    blueprint = {
        "interfaces": {
            "llm": {
                "default": {
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "directly_reads": ["SKILL.md"],
                    "directly_executes": [],
                    "directly_writes": [],
                }
            },
            "machine": {
                "worker": {
                    "directly_reads": [],
                    "directly_executes": ["_rtx/_worker.py"],
                    "directly_writes": [],
                }
            },
        }
    }

    labels = {
        item.label
        for item in health_state.DependencyExplorer(repo).explore_skill(skill, blueprint)
    }

    assert {
        "skills/demo-skill/SKILL.md",
        "skills/demo-skill/guide.md",
        "skills/demo-skill/_rtx/_worker.py",
        "skills/demo-skill/depends_on_skills",
        "skills/demo-skill/permissions.json",
    } <= labels


def test_python_route_smoke_imports_local_module_dependencies(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "_rtx" / "__init__.py", "PACKAGE_VALUE = 'one'\n")
    write(skill / "_rtx" / "_nested.py", "VALUE = 'one'\n")
    write(skill / "_rtx" / "_helper.py", "from . import _nested\nVALUE = _nested.VALUE\n")
    write(
        skill / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "from . import _helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        assert _helper.VALUE",
                "",
            ]
        ),
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": ["_rtx/_main.py"],
        "directly_writes": [],
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(skill, repo, spec)
    }

    assert "skills/demo-skill/_rtx/__init__.py" in labels
    assert "skills/demo-skill/_rtx/_main.py" in labels
    assert "skills/demo-skill/_rtx/_helper.py" in labels
    assert "skills/demo-skill/_rtx/_nested.py" in labels


def test_python_route_smoke_includes_package_init_files(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "_rtx" / "__init__.py", "PACKAGE_VALUE = 'one'\n")
    write(
        skill / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "from . import PACKAGE_VALUE",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        assert PACKAGE_VALUE",
                "",
            ]
        ),
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": ["_rtx/_main.py"],
        "directly_writes": [],
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(skill, repo, spec)
    }

    assert "skills/demo-skill/_rtx/__init__.py" in labels
    assert "skills/demo-skill/_rtx/_main.py" in labels


def test_python_route_smoke_includes_officina_imports(tmp_path: Path) -> None:
    repo = Path(__file__).resolve().parents[3]
    skill = tmp_path / "skills" / "demo-skill"
    write(skill / "_rtx" / "__init__.py", "")
    write(
        skill / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        from officina.common import dates",
                "        assert dates",
                "",
            ]
        ),
    )
    sys.modules.pop("officina.common.dates", None)
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": [],
        "directly_writes": [],
    }

    modules = [
        entry.label
        for entry in health_state.python_runtime_dependency_entries(skill, repo, spec)
    ]

    assert "src/officina/__init__.py" in modules
    assert "src/officina/common/__init__.py" in modules
    assert "src/officina/common/dates.py" in modules


def test_python_declared_dispatch_dependencies_are_traced_recursively(tmp_path: Path) -> None:
    repo = tmp_path
    source = repo / "skills" / "source-skill"
    target = repo / "skills" / "target-skill"
    write(
        source / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "depends_on:\n"
        "  target-skill:\n"
        "    major_version: 1\n"
        "    exports: [target-skill.machine.target]\n",
    )
    write(source / "_rtx" / "__init__.py", "")
    write(
        source / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface",
                "",
                "class Interface(PythonMachineInterface):",
                "    dispatches = {",
                "        'target': DispatchCall(",
                "            caller_skill='source-skill',",
                "            target_skill='target-skill',",
                "            interface='target',",
                "        )",
                "    }",
                "",
                "    def route_smoke(self):",
                "        return None",
                "",
            ]
        ),
    )
    write(target / "_rtx" / "__init__.py", "")
    write(target / "_rtx" / "_helper.py", "VALUE = 'ok'\n")
    write(
        target / "_rtx" / "_target.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        from . import _helper",
                "        assert _helper.VALUE",
                "",
            ]
        ),
    )
    write(
        target / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    target:\n"
        "      allowed_callers: [source-skill]\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_target.py:Interface\n",
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": ["_rtx/_main.py"],
        "directly_writes": [],
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(source, repo, spec)
    }

    assert "skills/target-skill/_rtx/__init__.py" in labels
    assert "skills/target-skill/_rtx/_target.py" in labels
    assert "skills/target-skill/_rtx/_helper.py" in labels


def test_python_declared_dispatch_to_command_runtime_hashes_command_file(tmp_path: Path) -> None:
    repo = tmp_path
    source = repo / "skills" / "source-skill"
    target = repo / "skills" / "target-skill"
    write(
        source / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "depends_on:\n"
        "  target-skill:\n"
        "    major_version: 1\n"
        "    exports: [target-skill.machine.command]\n",
    )
    write(source / "_rtx" / "__init__.py", "")
    write(
        source / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface",
                "",
                "class Interface(PythonMachineInterface):",
                "    dispatches = {",
                "        'command': DispatchCall(",
                "            caller_skill='source-skill',",
                "            target_skill='target-skill',",
                "            interface='command',",
                "            smoke_args=(),",
                "        )",
                "    }",
                "",
            ]
        ),
    )
    write(target / "_rtx" / "_tool.sh", "#!/usr/bin/env bash\ntrue\n")
    write(
        target / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    command:\n"
        "      allowed_callers: [source-skill]\n"
        "      patterns:\n"
        "        - min_positionals: 0\n"
        "          max_positionals: 0\n"
        "      runtime:\n"
        "        kind: command\n"
        "        argv: [_rtx/_tool.sh]\n",
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": ["_rtx/_main.py"],
        "directly_writes": [],
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(source, repo, spec)
    }

    assert "skills/target-skill/_rtx/_tool.sh" in labels


def test_python_mixed_local_officina_and_dispatched_imports_are_all_traced(tmp_path: Path) -> None:
    repo = tmp_path
    source = repo / "skills" / "source-skill"
    target = repo / "skills" / "target-skill"
    write(
        source / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "depends_on:\n"
        "  target-skill:\n"
        "    major_version: 1\n"
        "    exports: [target-skill.machine.target]\n",
    )
    write(source / "_rtx" / "__init__.py", "")
    write(
        source / "_rtx" / "_source_nested.py",
        "from officina.common import dates\nVALUE = dates\n",
    )
    write(
        source / "_rtx" / "_source_helper.py",
        "from . import _source_nested\nVALUE = _source_nested.VALUE\n",
    )
    write(
        source / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface",
                "from . import _source_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    dispatches = {",
                "        'target': DispatchCall(",
                "            caller_skill='source-skill',",
                "            target_skill='target-skill',",
                "            interface='target',",
                "        )",
                "    }",
                "",
                "    def route_smoke(self):",
                "        assert _source_helper.VALUE",
                "",
            ]
        ),
    )
    write(target / "_rtx" / "__init__.py", "")
    write(
        target / "_rtx" / "_target_nested.py",
        "from officina.common import toml_io\nVALUE = toml_io\n",
    )
    write(
        target / "_rtx" / "_target_helper.py",
        "from . import _target_nested\nVALUE = _target_nested.VALUE\n",
    )
    write(
        target / "_rtx" / "_target.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "from . import _target_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        assert _target_helper.VALUE",
                "",
            ]
        ),
    )
    write(
        target / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    target:\n"
        "      allowed_callers: [source-skill]\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_target.py:Interface\n",
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": ["_rtx/_main.py"],
        "directly_writes": [],
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(source, repo, spec)
    }

    expected = {
        "skills/source-skill/_rtx/__init__.py",
        "skills/source-skill/_rtx/_main.py",
        "skills/source-skill/_rtx/_source_helper.py",
        "skills/source-skill/_rtx/_source_nested.py",
        "skills/target-skill/_rtx/__init__.py",
        "skills/target-skill/_rtx/_target.py",
        "skills/target-skill/_rtx/_target_helper.py",
        "skills/target-skill/_rtx/_target_nested.py",
    }
    assert expected <= labels
    assert_label_suffix(labels, "src/officina/common/__init__.py")
    assert_label_suffix(labels, "src/officina/common/dates.py")
    assert_label_suffix(labels, "src/officina/common/toml_io.py")


def test_python_deep_dispatch_chain_preserves_each_hops_import_graph(tmp_path: Path) -> None:
    repo = tmp_path
    source = repo / "skills" / "source-skill"
    middle = repo / "skills" / "middle-skill"
    leaf = repo / "skills" / "leaf-skill"
    write(
        source / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "depends_on:\n"
        "  middle-skill:\n"
        "    major_version: 1\n"
        "    exports: [middle-skill.machine.middle]\n",
    )
    write(source / "_rtx" / "__init__.py", "")
    write(source / "_rtx" / "_source_helper.py", "VALUE = 'source'\n")
    write(
        source / "_rtx" / "_source.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface",
                "from . import _source_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    dispatches = {",
                "        'middle': DispatchCall(",
                "            caller_skill='source-skill',",
                "            target_skill='middle-skill',",
                "            interface='middle',",
                "        )",
                "    }",
                "",
                "    def route_smoke(self):",
                "        assert _source_helper.VALUE",
                "",
            ]
        ),
    )
    write(
        middle / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "depends_on:\n"
        "  leaf-skill:\n"
        "    major_version: 1\n"
        "    exports: [leaf-skill.machine.leaf]\n"
        "interfaces:\n"
        "  machine:\n"
        "    middle:\n"
        "      allowed_callers: [source-skill]\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_middle.py:Interface\n",
    )
    write(middle / "_rtx" / "__init__.py", "")
    write(middle / "_rtx" / "_middle_helper.py", "from officina.common import dates\nVALUE = dates\n")
    write(
        middle / "_rtx" / "_middle.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface",
                "from . import _middle_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    dispatches = {",
                "        'leaf': DispatchCall(",
                "            caller_skill='middle-skill',",
                "            target_skill='leaf-skill',",
                "            interface='leaf',",
                "        )",
                "    }",
                "",
                "    def route_smoke(self):",
                "        assert _middle_helper.VALUE",
                "",
            ]
        ),
    )
    write(
        leaf / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    leaf:\n"
        "      allowed_callers: [middle-skill]\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_leaf.py:Interface\n",
    )
    write(leaf / "_rtx" / "__init__.py", "")
    write(leaf / "_rtx" / "_leaf_nested.py", "from officina.common import dates\nVALUE = dates\n")
    write(leaf / "_rtx" / "_leaf_helper.py", "from . import _leaf_nested\nVALUE = _leaf_nested.VALUE\n")
    write(
        leaf / "_rtx" / "_leaf.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "from . import _leaf_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        assert _leaf_helper.VALUE",
                "",
            ]
        ),
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_source.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": ["_rtx/_source.py"],
        "directly_writes": [],
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(source, repo, spec)
    }

    expected = {
        "skills/source-skill/_rtx/__init__.py",
        "skills/source-skill/_rtx/_source.py",
        "skills/source-skill/_rtx/_source_helper.py",
        "skills/middle-skill/_rtx/__init__.py",
        "skills/middle-skill/_rtx/_middle.py",
        "skills/middle-skill/_rtx/_middle_helper.py",
        "skills/leaf-skill/_rtx/__init__.py",
        "skills/leaf-skill/_rtx/_leaf.py",
        "skills/leaf-skill/_rtx/_leaf_helper.py",
        "skills/leaf-skill/_rtx/_leaf_nested.py",
    }
    assert expected <= labels
    assert_label_suffix(labels, "src/officina/common/dates.py")


def test_python_branching_dispatches_and_multiple_imports_are_all_traced(tmp_path: Path) -> None:
    repo = tmp_path
    source = repo / "skills" / "source-skill"
    alpha = repo / "skills" / "alpha-skill"
    beta = repo / "skills" / "beta-skill"
    write(
        source / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "depends_on:\n"
        "  alpha-skill:\n"
        "    major_version: 1\n"
        "    exports: [alpha-skill.machine.alpha]\n"
        "  beta-skill:\n"
        "    major_version: 1\n"
        "    exports: [beta-skill.machine.beta]\n",
    )
    write(source / "_rtx" / "__init__.py", "")
    write(source / "_rtx" / "_first_helper.py", "VALUE = 'first'\n")
    write(source / "_rtx" / "_second_helper.py", "from officina.common import dates\nVALUE = dates\n")
    write(
        source / "_rtx" / "_main.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import DispatchCall, PythonMachineInterface",
                "from . import _first_helper, _second_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    dispatches = {",
                "        'alpha': DispatchCall(",
                "            caller_skill='source-skill',",
                "            target_skill='alpha-skill',",
                "            interface='alpha',",
                "        ),",
                "        'beta': DispatchCall(",
                "            caller_skill='source-skill',",
                "            target_skill='beta-skill',",
                "            interface='beta',",
                "        ),",
                "    }",
                "",
                "    def route_smoke(self):",
                "        assert _first_helper.VALUE",
                "        assert _second_helper.VALUE",
                "",
            ]
        ),
    )

    write(alpha / "_rtx" / "__init__.py", "")
    write(alpha / "_rtx" / "_alpha_helper.py", "from officina.common import toml_io\nVALUE = toml_io\n")
    write(
        alpha / "_rtx" / "_alpha.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "from . import _alpha_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        assert _alpha_helper.VALUE",
                "",
            ]
        ),
    )
    write(
        alpha / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    alpha:\n"
        "      allowed_callers: [source-skill]\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_alpha.py:Interface\n",
    )

    write(beta / "_rtx" / "__init__.py", "")
    write(beta / "_rtx" / "_beta_nested.py", "VALUE = 'beta'\n")
    write(beta / "_rtx" / "_beta_helper.py", "from . import _beta_nested\nVALUE = _beta_nested.VALUE\n")
    write(
        beta / "_rtx" / "_beta.py",
        "\n".join(
            [
                "from officina.runtime.python_machine_interface import PythonMachineInterface",
                "from . import _beta_helper",
                "",
                "class Interface(PythonMachineInterface):",
                "    def route_smoke(self):",
                "        assert _beta_helper.VALUE",
                "",
            ]
        ),
    )
    write(
        beta / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    beta:\n"
        "      allowed_callers: [source-skill]\n"
        "      runtime:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_beta.py:Interface\n",
    )
    spec = {
        "runtime": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
        "directly_reads": [],
        "directly_executes": ["_rtx/_main.py"],
        "directly_writes": [],
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(source, repo, spec)
    }

    expected = {
        "skills/source-skill/_rtx/__init__.py",
        "skills/source-skill/_rtx/_main.py",
        "skills/source-skill/_rtx/_first_helper.py",
        "skills/source-skill/_rtx/_second_helper.py",
        "skills/alpha-skill/_rtx/__init__.py",
        "skills/alpha-skill/_rtx/_alpha.py",
        "skills/alpha-skill/_rtx/_alpha_helper.py",
        "skills/beta-skill/_rtx/__init__.py",
        "skills/beta-skill/_rtx/_beta.py",
        "skills/beta-skill/_rtx/_beta_helper.py",
        "skills/beta-skill/_rtx/_beta_nested.py",
    }
    assert expected <= labels
    assert_label_suffix(labels, "src/officina/common/dates.py")
    assert_label_suffix(labels, "src/officina/common/toml_io.py")
