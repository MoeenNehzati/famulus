from __future__ import annotations

import importlib.util
import sys
from pathlib import Path



MODULE_PATH = Path(__file__).resolve().parents[1] / "_rtx" / "_drift_hashes.py"
SPEC = importlib.util.spec_from_file_location("skill_drift_hashes", MODULE_PATH)
health_state = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = health_state
SPEC.loader.exec_module(health_state)


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def assert_label_suffix(labels: set[str], suffix: str) -> None:
    assert any(label.endswith(suffix) for label in labels), suffix


def test_explore_file_does_not_chase_markdown_references(tmp_path: Path) -> None:
    repo = tmp_path
    write(
        repo / "root.md",
        "\n".join(
            [
                "See [alpha](alpha.beta.md), [slash](dir/slash.md), and @win/path.txt.",
                "Bare prose docs/prose.md is an existing dependency.",
                "Bare prose missing.md is not a dependency because it is missing.",
                "@missing.md is an explicit missing dependency.",
            ]
        ),
    )
    write(repo / "alpha.beta.md", "Then read second.md.\n")
    write(repo / "second.md", "done\n")
    write(repo / "dir" / "slash.md", "slash\n")
    write(repo / "win" / "path.txt", "windows path\n")
    write(repo / "docs" / "prose.md", "prose path\n")

    labels = {
        item.label
        for item in health_state.DependencyExplorer(repo).explore_file(repo / "root.md", base_dir=repo)
    }

    assert labels == {
        "root.md",
    }


def test_markdown_links_code_and_explicit_refs_are_not_drift_dependencies(tmp_path: Path) -> None:
    repo = tmp_path
    write(
        repo / "docs" / "root.md",
        "\n".join(
            [
                "[guide](../refs/guide.md#section).",
                "![image](../assets/img.png)",
                "<https://example.com/x.md>",
                "`[not](../refs/code.md)`",
                "`../refs/code-path.md`",
                "```",
                "[not](../refs/fence.md)",
                "../refs/fence-path.md",
                "@../refs/fence-explicit.md",
                "```",
                "@../refs/explicit.md.",
                "@/abs/path.md",
                "@../../outside.md",
                "[missing](../refs/missing.md)",
                "@../refs/missing-explicit.md",
            ]
        ),
    )
    write(repo / "refs" / "guide.md", "guide\n")
    write(repo / "refs" / "explicit.md", "explicit\n")
    write(repo / "refs" / "code-path.md", "code path\n")
    write(repo / "refs" / "fence-path.md", "fence path\n")
    write(repo / "assets" / "img.png", "img\n")

    labels = {
        item.label
        for item in health_state.DependencyExplorer(repo).explore_file(
            repo / "docs" / "root.md",
            base_dir=repo / "docs",
        )
    }

    assert labels == {
        "docs/root.md",
    }


def test_transitive_markdown_references_are_not_drift_dependencies(tmp_path: Path) -> None:
    repo = tmp_path
    write(repo / "skills" / "demo-skill" / "SKILL.md", "@../../references/standard.md\n")
    write(repo / "references" / "standard.md", "`blueprint/schema.json`\n")
    write(repo / "references" / "blueprint" / "schema.json", "{}\n")

    labels = {
        item.label
        for item in health_state.DependencyExplorer(repo).explore_file(
            repo / "skills" / "demo-skill" / "SKILL.md",
            base_dir=repo / "skills" / "demo-skill",
        )
    }

    assert labels == {
        "skills/demo-skill/SKILL.md",
    }


def test_explore_interface_unions_behavior_sources_and_python_dependencies(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "NOTES.md", "See [extra](references/extra.md).\n")
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
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
            "behavior_sources": [
                {
                    "path": "NOTES.md",
                    "content": "config",
                    "format": "markdown",
                    "reason": "Controls the test interface behavior.",
                }
            ],
        },
    }

    labels = {
        item.label
        for item in health_state.DependencyExplorer(repo).explore_interface(skill, spec)
    }

    assert {
        "skills/demo-skill/NOTES.md",
        "skills/demo-skill/_rtx/__init__.py",
        "skills/demo-skill/_rtx/_main.py",
        "skills/demo-skill/_rtx/_helper.py",
    } <= labels


def test_explore_skill_unions_interface_files(tmp_path: Path) -> None:
    repo = tmp_path
    skill = repo / "skills" / "demo-skill"
    write(skill / "SKILL.md", "See @guide.md.\n")
    write(skill / "guide.md", "guide\n")
    write(skill / "schemas" / "worker.json", "{}\n")
    write(skill / "_rtx" / "__init__.py", "")
    write(
        skill / "_rtx" / "_worker.py",
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n\n"
        "class Interface(PythonMachineInterface):\n"
        "    pass\n",
    )
    blueprint = {
        "interfaces": {
            "llm": {
                "default": {
                    "binding": {"kind": "skill_file", "path": "SKILL.md"},
                    "behavior_sources": [],
                }
            },
            "machine": {
                "worker": {
                    "invocation": {
                        "kind": "python_machine_interface",
                        "entrypoint": "_rtx/_worker.py:Interface",
                        "behavior_sources": [
                            {
                                "path": "schemas/worker.json",
                                "content": "validator",
                                "format": "json",
                                "reason": "Defines worker behavior.",
                            }
                        ],
                    },
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
        "skills/demo-skill/schemas/worker.json",
        "skills/demo-skill/_rtx/_worker.py",
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
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
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
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
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
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
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
        "    exports: [target-skill.machine.target]\n"
        "interfaces:\n"
        "  machine:\n"
        "    source:\n"
        "      version: 1\n"
        "      uses_interfaces:\n"
        "        - interface: target-skill.machine.target\n"
        "          version: 1\n",
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
        "      version: 1\n"
        "      allowed_callers: [source-skill]\n"
        "      invocation:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_target.py:Interface\n",
    )
    spec = {
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(source, repo, spec)
    }

    assert "skills/target-skill/_rtx/__init__.py" in labels
    assert "skills/target-skill/_rtx/_target.py" in labels
    assert "skills/target-skill/_rtx/_helper.py" in labels


def test_python_declared_dispatch_to_python_runtime_hashes_target_runtime_file(tmp_path: Path) -> None:
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
        "    exports: [target-skill.machine.run]\n"
        "interfaces:\n"
        "  machine:\n"
        "    source:\n"
        "      version: 1\n"
        "      uses_interfaces:\n"
        "        - interface: target-skill.machine.run\n"
        "          version: 1\n",
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
                "        'run': DispatchCall(",
                "            caller_skill='source-skill',",
                "            target_skill='target-skill',",
                "            interface='run',",
                "            smoke_args=(),",
                "        )",
                "    }",
                "",
            ]
        ),
    )
    write(target / "_rtx" / "_tool.py", "from officina.runtime.python_machine_interface import PythonMachineInterface\n\nclass Interface(PythonMachineInterface):\n    pass\n")
    write(
        target / "blueprint.yaml",
        "category: workflow-general-assistant\n"
        "interface_version: 1\n"
        "interfaces:\n"
        "  machine:\n"
        "    run:\n"
        "      version: 1\n"
        "      allowed_callers: [source-skill]\n"
        "      patterns:\n"
        "        - min_positionals: 0\n"
        "          max_positionals: 0\n"
        "      invocation:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_tool.py:Interface\n"
    )
    spec = {
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
    }

    labels = {
        entry.label
        for entry in health_state.python_runtime_dependency_entries(source, repo, spec)
    }

    assert "skills/target-skill/_rtx/_tool.py" in labels


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
        "    exports: [target-skill.machine.target]\n"
        "interfaces:\n"
        "  machine:\n"
        "    source:\n"
        "      version: 1\n"
        "      uses_interfaces:\n"
        "        - interface: target-skill.machine.target\n"
        "          version: 1\n",
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
        "      version: 1\n"
        "      allowed_callers: [source-skill]\n"
        "      invocation:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_target.py:Interface\n",
    )
    spec = {
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
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
        "    exports: [middle-skill.machine.middle]\n"
        "interfaces:\n"
        "  machine:\n"
        "    source:\n"
        "      version: 1\n"
        "      uses_interfaces:\n"
        "        - interface: middle-skill.machine.middle\n"
        "          version: 1\n",
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
        "      version: 1\n"
        "      uses_interfaces:\n"
        "        - interface: leaf-skill.machine.leaf\n"
        "          version: 1\n"
        "      allowed_callers: [source-skill]\n"
        "      invocation:\n"
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
        "      version: 1\n"
        "      allowed_callers: [middle-skill]\n"
        "      invocation:\n"
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
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_source.py:Interface",
        },
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
        "    exports: [beta-skill.machine.beta]\n"
        "interfaces:\n"
        "  machine:\n"
        "    source:\n"
        "      version: 1\n"
        "      uses_interfaces:\n"
        "        - interface: alpha-skill.machine.alpha\n"
        "          version: 1\n"
        "        - interface: beta-skill.machine.beta\n"
        "          version: 1\n",
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
        "      version: 1\n"
        "      allowed_callers: [source-skill]\n"
        "      invocation:\n"
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
        "      version: 1\n"
        "      allowed_callers: [source-skill]\n"
        "      invocation:\n"
        "        kind: python_machine_interface\n"
        "        entrypoint: _rtx/_beta.py:Interface\n",
    )
    spec = {
        "invocation": {
            "kind": "python_machine_interface",
            "entrypoint": "_rtx/_main.py:Interface",
        },
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
