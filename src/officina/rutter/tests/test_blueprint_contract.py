"""Blueprint ownership and public-contract tests owned by Rutter."""

from __future__ import annotations

from pathlib import Path

import yaml

import officina.blueprints.graph as blueprint_graph
from officina.blueprints.graph import load_repository_blueprint_graph
from officina.blueprints.inventory import collect_blueprints


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
CANONICAL_SCHEMA_ROOT = REPOSITORY_ROOT / "references" / "blueprint"


def test_v6_rutter_operation_effects_are_outcome_specific() -> None:
    """Dry-run, create, and replacement outcomes declare their exact effects."""

    repo_root = REPOSITORY_ROOT
    engine_path = repo_root / "src/officina/rutter/blueprints/engine.yaml"
    storage_path = repo_root / "src/officina/rutter/blueprints/storage.yaml"
    engine = yaml.safe_load(engine_path.read_text(encoding="utf-8"))
    storage = yaml.safe_load(storage_path.read_text(encoding="utf-8"))
    validators = {}
    for path, document in ((engine_path, engine), (storage_path, storage)):
        assert blueprint_graph._declaration_schema_errors(
            path,
            document,
            CANONICAL_SCHEMA_ROOT,
            validators,
            expected_schema_version=6,
        ) == ()

    bound = engine["interfaces"][
        "rutter.source.engine.interface.bound-operations"
    ]
    assert bound["version"] == 6
    assert "inquisitive-inventory CLI" in bound["description"]
    bound_contract = bound["contract"]
    bound_outcomes = {
        entry["id"]: entry for entry in bound_contract["outcomes"]
    }
    assert bound_outcomes["described"]["effects"] == []
    assert bound_outcomes["previewed"]["effects"] == []
    for outcome in ("ready", "terminal", "faulted", "uncertain"):
        assert bound_outcomes[outcome]["effects"] == ["reckoning-update"]
    bound_effects = {
        entry["id"]: entry
        for entry in bound_contract["execution"]["effects"]
    }
    assert bound_effects["reckoning-update"]["may_occur_in_outcomes"] == [
        "ready",
        "terminal",
        "faulted",
        "uncertain",
    ]

    write = storage["interfaces"][
        "rutter.source.storage.interface.write"
    ]["contract"]
    write_outcomes = {entry["id"]: entry for entry in write["outcomes"]}
    assert write_outcomes["created"]["effects"] == ["reckoning-file-create"]
    assert write_outcomes["replaced"]["effects"] == ["reckoning-file-replace"]
    write_effects = {entry["id"]: entry for entry in write["execution"]["effects"]}
    assert write_effects["reckoning-file-create"]["action"] == "create"
    assert write_effects["reckoning-file-create"]["may_occur_in_outcomes"] == [
        "created"
    ]
    assert write_effects["reckoning-file-replace"]["action"] == "update"
    assert write_effects["reckoning-file-replace"]["may_occur_in_outcomes"] == [
        "replaced"
    ]


def test_v6_rutter_bound_operations_names_response_required_boundary() -> None:
    """A missing LLMStep response is a boundary, not invalid submitted input."""

    repo_root = REPOSITORY_ROOT
    engine = yaml.safe_load(
        (repo_root / "src/officina/rutter/blueprints/engine.yaml").read_text(
            encoding="utf-8"
        )
    )
    contract = engine["interfaces"][
        "rutter.source.engine.interface.bound-operations"
    ]["contract"]
    outcomes = {entry["id"]: entry for entry in contract["outcomes"]}

    response_required = outcomes["response-required"]
    assert response_required["class"] == "refusal"
    assert response_required["effects"] == []
    assert "RutterValidationError: LLMStep response is required" in response_required[
        "caller_action"
    ]
    assert "VoyageStatus" in response_required["caller_action"]
    assert "get_status" in response_required["caller_action"]
    assert "perform the LLM instruction" in response_required["caller_action"]
    assert "does not return a ValidationReport" in response_required["caller_action"]
    assert "ValidationReport" in outcomes["invalid-input"]["caller_action"]


def test_v6_rutter_blueprints_split_exact_implementation_ownership() -> None:
    """The cohesive implementation files own exact imports and interfaces."""

    repo_root = REPOSITORY_ROOT
    graph = load_repository_blueprint_graph(
        repo_root,
        expected_schema_version=6,
    )
    rutter_root = repo_root / "src/officina/rutter"
    module = yaml.safe_load((rutter_root / "blueprint.yaml").read_text(encoding="utf-8"))
    source_names = (
        "authoring",
        "diagnostic",
        "dispenser",
        "engine",
        "evaluation",
        "history",
        "model",
        "reducer",
        "runtime",
        "storage",
        "values",
    )
    sources = {
        name: yaml.safe_load(
            (rutter_root / "blueprints" / f"{name}.yaml").read_text(encoding="utf-8")
        )
        for name in source_names
    }
    common = yaml.safe_load(
        (rutter_root.parent / "common" / "blueprint.yaml").read_text(encoding="utf-8")
    )

    assert module["version"] == 10
    assert sources["diagnostic"]["version"] == 4
    assert sources["diagnostic"]["interfaces"][
        "rutter.source.diagnostic.interface.python-api"
    ]["version"] == 4
    assert sources["dispenser"]["version"] == 5
    dispenser_interface = sources["dispenser"]["interfaces"][
        "rutter.source.dispenser.interface.python-api"
    ]
    assert dispenser_interface["version"] == 5
    assert "run-id" in dispenser_interface["contract"]["arguments"]
    assert module["content"] == [
        r"__init__\.py",
        r"authoring\.py",
        r"diagnostic\.py",
        r"dispenser\.py",
        r"engine\.py",
        r"evaluation\.py",
        r"history\.py",
        r"model\.py",
        r"reducer\.py",
        r"runtime\.py",
        r"storage\.py",
        r"tests/.*",
        r"values\.py",
    ]
    assert set(module["sources"]) == {
        "rutter.source.authoring",
        "rutter.source.diagnostic",
        "rutter.source.dispenser",
        "rutter.source.engine",
        "rutter.source.evaluation",
        "rutter.source.history",
        "rutter.source.model",
        "rutter.source.reducer",
        "rutter.source.runtime",
        "rutter.source.storage",
        "rutter.source.values",
    }
    assert {"rutter", *(f"rutter.source.{name}" for name in source_names)}.issubset(
        graph.nodes
    )
    assert "rutter.source.hooks" not in graph.nodes
    assert set(module["exports"]) == {
        "rutter.interface.binding",
        "rutter.interface.bound-operations",
        "rutter.interface.diagnostic",
        "rutter.interface.dispenser",
        "rutter.interface.model",
    }
    expected_callers = {
        "rutter.interface.binding": {"math-dependency-graph._rtx"},
        "rutter.interface.bound-operations": {
            "math-dependency-graph._rtx",
            "using-compass",
        },
        "rutter.interface.diagnostic": {"math-dependency-graph._rtx"},
        "rutter.interface.dispenser": {
            "math-dependency-graph._rtx",
            "using-compass",
        },
        "rutter.interface.model": {"math-dependency-graph._rtx"},
    }
    for interface_id, callers in expected_callers.items():
        access = module["exports"][interface_id]["access"]
        assert access["allow_all_modules"] is False
        assert set(access["allowed_callers"]) == callers
    expected_runtime_dependencies = {
        "evaluation": [
            {
                "kind": "python-package",
                "name": "jsonschema",
                "platforms": {
                    "linux": True,
                    "macos": True,
                    "windows": True,
                },
                "reason": (
                    "Validates complete flat LLMStep responses before contextual "
                    "assessment."
                ),
                "version": ">=4,<5",
            }
        ]
    }
    for name, source in sources.items():
        assert source["gateway"] == {"path": f"{name}.py", "language": "Python"}
        assert source["content"] == [rf"{name}\.py"]
        assert source["runtime_dependencies"] == expected_runtime_dependencies.get(
            name, []
        )

    expected_interfaces = {
        "authoring": {"rutter.source.authoring.interface.python-api"},
        "diagnostic": {"rutter.source.diagnostic.interface.python-api"},
        "dispenser": {"rutter.source.dispenser.interface.python-api"},
        "engine": {
            "rutter.source.engine.interface.binding",
            "rutter.source.engine.interface.bound-operations",
        },
        "evaluation": {"rutter.source.evaluation.interface.python-api"},
        "history": {"rutter.source.history.interface.python-api"},
        "model": {"rutter.source.model.interface.python-api"},
        "reducer": {"rutter.source.reducer.interface.python-api"},
        "runtime": {"rutter.source.runtime.interface.binding"},
        "storage": {
            "rutter.source.storage.interface.read",
            "rutter.source.storage.interface.transaction",
            "rutter.source.storage.interface.write",
        },
        "values": {"rutter.source.values.interface.python-api"},
    }
    for name, interface_ids in expected_interfaces.items():
        assert set(sources[name]["interfaces"]) == interface_ids

    expected_dependencies = {
        "authoring": ["rutter.source.history", "rutter.source.values"],
        "diagnostic": ["rutter.source.authoring", "rutter.source.values"],
        "dispenser": ["rutter.source.engine", "rutter.source.values"],
        "engine": [
            "rutter.source.authoring",
            "rutter.source.evaluation",
            "rutter.source.history",
            "rutter.source.reducer",
            "rutter.source.storage",
            "rutter.source.values",
        ],
        "evaluation": [
            "rutter.source.authoring",
            "rutter.source.history",
            "rutter.source.values",
        ],
        "history": ["rutter.source.values"],
        "model": [
            "rutter.source.authoring",
            "rutter.source.history",
            "rutter.source.values",
        ],
        "reducer": ["rutter.source.history", "rutter.source.values"],
        "runtime": [
            "rutter.source.authoring",
            "rutter.source.engine",
            "rutter.source.storage",
            "rutter.source.values",
        ],
        "storage": [
            "rutter.source.history",
            "rutter.source.values",
            "common.source.atomic-files",
        ],
        "values": [],
    }
    expected_uses_interfaces = {
        "authoring": [
            {"interface": "rutter.source.history.interface.python-api", "version": 1},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "diagnostic": [
            {"interface": "rutter.source.authoring.interface.python-api", "version": 1},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "dispenser": [
            {"interface": "rutter.source.engine.interface.bound-operations", "version": 6},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "engine": [
            {"interface": "rutter.source.authoring.interface.python-api", "version": 1},
            {"interface": "rutter.source.evaluation.interface.python-api", "version": 1},
            {"interface": "rutter.source.history.interface.python-api", "version": 1},
            {"interface": "rutter.source.reducer.interface.python-api", "version": 1},
            {"interface": "rutter.source.storage.interface.read", "version": 2},
            {"interface": "rutter.source.storage.interface.transaction", "version": 2},
            {"interface": "rutter.source.storage.interface.write", "version": 2},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "evaluation": [
            {"interface": "rutter.source.authoring.interface.python-api", "version": 1},
            {"interface": "rutter.source.history.interface.python-api", "version": 1},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "history": [
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "model": [
            {"interface": "rutter.source.authoring.interface.python-api", "version": 1},
            {"interface": "rutter.source.history.interface.python-api", "version": 1},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "reducer": [
            {"interface": "rutter.source.history.interface.python-api", "version": 1},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "runtime": [
            {"interface": "rutter.source.authoring.interface.python-api", "version": 1},
            {"interface": "rutter.source.engine.interface.binding", "version": 3},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
        ],
        "storage": [
            {"interface": "rutter.source.history.interface.python-api", "version": 1},
            {"interface": "rutter.source.values.interface.python-api", "version": 1},
            {"interface": "common.interface.atomic-files", "version": 1},
        ],
        "values": [],
    }
    for name, dependencies in expected_dependencies.items():
        assert [entry["source"] for entry in sources[name]["dependencies"]] == dependencies
        assert sources[name]["uses_interfaces"] == expected_uses_interfaces[name]

    diagnostic_contract = sources["diagnostic"]["interfaces"][
        "rutter.source.diagnostic.interface.python-api"
    ]["contract"]
    assert {
        entry["value"]
        for entry in diagnostic_contract["arguments"]["operation"]["type"]["values"]
    } == {
        "question-case",
        "diagnosis-case",
        "diagnosis-detail",
        "diagnose-answer",
        "ask-and-diagnose",
        "transition-hook",
    }

    final_contract_text = "\n".join(
        (rutter_root / "blueprints" / f"{name}.yaml").read_text(encoding="utf-8")
        for name in source_names
    )
    assert "Fix" not in final_contract_text
    assert "BaseRutter" not in final_contract_text

    engine_interfaces = sources["engine"]["interfaces"]
    binding = engine_interfaces["rutter.source.engine.interface.binding"]["contract"]
    binding_operations = {
        entry["value"] for entry in binding["arguments"]["operation"]["type"]["values"]
    }
    binding_outcomes = {entry["id"]: entry for entry in binding["outcomes"]}
    assert binding_operations == {"create", "open"}
    assert binding_outcomes["created"]["effects"] == ["reckoning-create"]
    assert binding_outcomes["opened"]["effects"] == []

    bound = engine_interfaces[
        "rutter.source.engine.interface.bound-operations"
    ]
    assert bound["version"] == 6
    assert "inquisitive-inventory CLI" in bound["description"]
    bound_contract = bound["contract"]
    assert set(bound_contract["arguments"]) == {
        "operation",
        "binding",
        "value",
        "responding-to",
        "continue",
        "dry-run",
    }
    bound_operations = {
        entry["value"]
        for entry in bound_contract["arguments"]["operation"]["type"]["values"]
    }
    bound_outcomes = {
        entry["id"]: entry for entry in bound_contract["outcomes"]
    }
    assert bound_operations == {
        "help",
        "get-status",
        "validate",
        "advance",
    }
    assert "Help text" in bound_contract["outputs"][0]["description"]
    assert bound_outcomes["described"]["effects"] == []
    assert "VoyageStatus" in bound_contract["outputs"][0]["description"]
    assert bound_outcomes["observed"]["effects"] == []
    assert bound_outcomes["validated"]["effects"] == []
    assert bound_outcomes["previewed"]["effects"] == []
    response_required = bound_outcomes["response-required"]
    assert response_required["class"] == "refusal"
    assert response_required["effects"] == []
    assert "RutterValidationError: LLMStep response is required" in response_required[
        "caller_action"
    ]
    assert "VoyageStatus" in response_required["caller_action"]
    assert "get_status" in response_required["caller_action"]
    assert "perform the LLM instruction" in response_required["caller_action"]
    assert "does not return a ValidationReport" in response_required["caller_action"]
    assert bound_outcomes["invalid-input"]["effects"] == []
    assert "ValidationReport" in bound_outcomes["invalid-input"]["caller_action"]
    for outcome in ("ready", "terminal", "faulted", "uncertain"):
        assert bound_outcomes[outcome]["effects"] == ["reckoning-update"]
    bound_effects = {
        entry["id"]: entry
        for entry in bound_contract["execution"]["effects"]
    }
    assert bound_effects["reckoning-update"]["may_occur_in_outcomes"] == [
        "ready",
        "terminal",
        "faulted",
        "uncertain",
    ]

    storage_interfaces = sources["storage"]["interfaces"]
    read = storage_interfaces["rutter.source.storage.interface.read"]["contract"]
    transaction = storage_interfaces[
        "rutter.source.storage.interface.transaction"
    ]["contract"]
    write = storage_interfaces["rutter.source.storage.interface.write"]["contract"]
    assert read["execution"]["state_effect"] == "read-only"
    assert read["outputs"][0]["cardinality"] == {"minimum": 1, "maximum": 1}
    assert read["outcomes"][0]["effects"] == []
    assert transaction["execution"]["state_effect"] == "read-only"
    assert transaction["outcomes"][0]["effects"] == []
    assert write["outputs"] == []
    write_outcomes = {entry["id"]: entry for entry in write["outcomes"]}
    assert write_outcomes["created"]["outputs"] == []
    assert write_outcomes["created"]["effects"] == ["reckoning-file-create"]
    assert write_outcomes["replaced"]["outputs"] == []
    assert write_outcomes["replaced"]["effects"] == ["reckoning-file-replace"]
    write_effects = {entry["id"]: entry for entry in write["execution"]["effects"]}
    assert write_effects["reckoning-file-create"]["action"] == "create"
    assert write_effects["reckoning-file-create"]["may_occur_in_outcomes"] == [
        "created"
    ]
    assert write_effects["reckoning-file-replace"]["action"] == "update"
    assert write_effects["reckoning-file-replace"]["may_occur_in_outcomes"] == [
        "replaced"
    ]
    atomic_callers = common["exports"]["common.interface.atomic-files"]["access"][
        "allowed_callers"
    ]
    assert "rutter" in atomic_callers
    assert "using-compass" not in atomic_callers


def test_inventory_registers_exact_rutter_module_and_source_files() -> None:
    """A missing or broadened Rutter registration would orphan owned code."""

    result = collect_blueprints(REPOSITORY_ROOT, expected_schema_version=6)
    by_id = {document.node_id: document for document in result.documents}

    module = by_id["rutter"]
    source_names = (
        "authoring",
        "diagnostic",
        "dispenser",
        "engine",
        "evaluation",
        "history",
        "model",
        "reducer",
        "runtime",
        "storage",
        "values",
    )
    sources = {name: by_id[f"rutter.source.{name}"] for name in source_names}

    assert module.relative_path.as_posix() == "src/officina/rutter/blueprint.yaml"
    assert module.declaration["content"] == [
        r"__init__\.py",
        r"authoring\.py",
        r"diagnostic\.py",
        r"dispenser\.py",
        r"engine\.py",
        r"evaluation\.py",
        r"history\.py",
        r"model\.py",
        r"reducer\.py",
        r"runtime\.py",
        r"storage\.py",
        r"tests/.*",
        r"values\.py",
    ]
    assert set(module.declaration["sources"]) == {
        f"rutter.source.{name}" for name in source_names
    }
    for name, source in sources.items():
        assert source.declaration["gateway"] == {
            "path": f"{name}.py",
            "language": "Python",
        }
        assert source.declaration["content"] == [rf"{name}\.py"]
