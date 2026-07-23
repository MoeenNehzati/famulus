from __future__ import annotations

from pathlib import Path

import pytest

from officina.common.blueprint_graph import BlueprintNode, InterfaceExport
from officina.common.process_binding_compiler import (
    ProcessBindingError,
    compile_gateway_invocation,
    compile_route_smoke_invocation,
    gateway_language_name,
    parse_caller_invocation,
    select_authored_argv_pattern,
)


def _argument(
    binding: dict[str, object],
    type_spec: dict[str, object],
    *,
    required: bool = True,
    default: object = None,
    sensitivity: str = "public",
) -> dict[str, object]:
    result: dict[str, object] = {
        "description": "Argument.",
        "required": required,
        "sensitivity": sensitivity,
        "invocation_binding": binding,
        "type": type_spec,
    }
    if not required:
        result["default"] = default
    return result


@pytest.mark.parametrize(
    ("requirement", "expected"),
    [
        ("Python", "Python"),
        ("Python>=3.11", "Python"),
        ("Python>=3.11,<4", "Python"),
    ],
)
def test_gateway_language_name_uses_requirement_name(
    requirement: str,
    expected: str,
) -> None:
    assert gateway_language_name(requirement) == expected


def _export(
    arguments: dict[str, dict[str, object]],
    fixed: list[dict[str, object]] | None = None,
) -> InterfaceExport:
    contract_arguments: dict[str, dict[str, object]] = {}
    process_arguments: dict[str, dict[str, object]] = {}
    for argument_id, authored in arguments.items():
        declaration = dict(authored)
        process_arguments[argument_id] = declaration.pop("invocation_binding")
        contract_arguments[argument_id] = declaration
    declaration = {
        "version": 1,
        "process_binding": {
            "kind": "process",
            "arguments": process_arguments,
            "fixed": fixed or [],
        },
        "contract": {"arguments": contract_arguments},
    }
    return InterfaceExport(
        interface_id="demo-skill.interface.run",
        version=1,
        local_name="run",
        module_node_id="demo-skill",
        declaration=declaration,
        source_node_id="demo-skill.source.worker",
        source_interface_id="demo-skill.source.worker.interface.run",
    )


def _source() -> BlueprintNode:
    root = Path("/repo/skills/demo-skill")
    return BlueprintNode(
        node_id="demo-skill.source.worker",
        node_type="behavioral_source",
        version=1,
        skill_root=root,
        blueprint_path=root / "blueprints" / "worker.yaml",
        gateway_path=root / "_rtx" / "_worker.py",
        declaration={"gateway": {"path": "_rtx/_worker.py", "language": "Python"}},
    )


def _v4_export() -> tuple[BlueprintNode, InterfaceExport]:
    root = Path("/repo/skills/demo-skill")
    source_id = "demo-skill.source.worker"
    source = BlueprintNode(
        node_id=source_id,
        node_type="behavioral_source",
        version=1,
        skill_root=root,
        blueprint_path=root / "blueprints" / "worker.yaml",
        gateway_path=root / "_rtx" / "worker.py",
        declaration={
            "gateway": {"path": "_rtx/worker.py", "language": "Python>=3.11"}
        },
    )
    declaration = {
        "version": 1,
        "description": "Run.",
        "contract": {
            "arguments": {
                "count": {
                    "description": "Count.",
                    "required": True,
                    "sensitivity": "public",
                    "type": {"kind": "integer", "minimum": 1},
                }
            }
        },
        "process_binding": {
            "kind": "process",
            "entry": "Interface",
            "args_prefix": ["run"],
            "arguments": {
                "count": {
                    "kind": "option",
                    "name": "--count",
                    "arity": {"minimum": 1, "maximum": 1},
                }
            },
            "fixed": [
                {
                    "kind": "option",
                    "name": "--format",
                    "value": "json",
                    "type": {"kind": "string"},
                }
            ],
        },
    }
    return source, InterfaceExport(
        interface_id="demo-skill.interface.run",
        version=1,
        local_name="run",
        module_node_id="demo-skill",
        declaration=declaration,
        source_node_id=source_id,
        source_interface_id=f"{source_id}.interface.run",
        export_declaration={
            "source_interface": f"{source_id}.interface.run",
            "access": {"allow_all_modules": True, "allowed_callers": []},
        },
    )


def _v4_pattern_export(
    patterns: list[dict[str, object]] | None,
) -> tuple[BlueprintNode, InterfaceExport]:
    source, base = _v4_export()
    process_binding: dict[str, object] = {
        "kind": "process",
        "entry": "Interface",
        "args_prefix": ["audit"],
        "arguments": {},
        "fixed": [],
    }
    if patterns is not None:
        process_binding["patterns"] = patterns
    declaration = {
        **base.declaration,
        "contract": {"arguments": {}},
        "process_binding": process_binding,
    }
    return source, InterfaceExport(
        interface_id=base.interface_id,
        version=base.version,
        local_name=base.local_name,
        module_node_id=base.module_node_id,
        declaration=declaration,
        source_node_id=base.source_node_id,
        source_interface_id=base.source_interface_id,
        export_declaration=base.export_declaration,
    )


def test_v4_parses_contract_values_and_compiles_separate_process_binding() -> None:
    source, export = _v4_export()

    parsed = parse_caller_invocation(
        export, ["--count", "2"], stdin_requested=False
    )
    plan = compile_gateway_invocation(source, export, parsed)

    assert parsed.values == {"count": 2}
    assert plan.entry == "Interface"
    assert plan.argv == ("run", "--count", "2", "--format", "json")


def test_v4_route_smoke_compiles_without_required_caller_arguments() -> None:
    source, export = _v4_export()

    plan = compile_route_smoke_invocation(source, export)

    assert plan.entry == "Interface"
    assert plan.stdin_argument_id is None
    assert plan.argv == ("run", "--format", "json", "--route-smoke")


def test_v4_authored_pattern_validates_and_preserves_raw_argv() -> None:
    source, export = _v4_pattern_export(
        [
            {
                "name": "hashes",
                "min_positionals": 1,
                "allow_stdin": False,
                "allowed_flags": ["--json"],
            }
        ]
    )

    parsed = parse_caller_invocation(
        export, ["compute-hashes", "--json"], stdin_requested=False
    )
    plan = compile_gateway_invocation(source, export, parsed)

    assert parsed.values == {}
    assert parsed.raw_argv == ("compute-hashes", "--json")
    assert parsed.pattern_name == "hashes"
    assert plan.argv == ("audit", "compute-hashes", "--json")
    assert plan.pattern_name == "hashes"


def test_v4_authored_pattern_route_smoke_allows_semantic_contract_arguments() -> None:
    source, export = _v4_pattern_export(
        [{"name": "command", "min_positionals": 1}]
    )
    export.declaration["contract"]["arguments"]["command"] = {
        "description": "Command and options described by the semantic contract.",
        "required": True,
        "sensitivity": "public",
        "type": {"kind": "string"},
    }

    plan = compile_route_smoke_invocation(source, export)

    assert plan.argv == ("audit", "--route-smoke")


def test_v4_authored_pattern_raw_argv_allows_semantic_contract_arguments() -> None:
    source, export = _v4_pattern_export(
        [{"name": "command", "min_positionals": 1}]
    )
    export.declaration["contract"]["arguments"]["command"] = {
        "description": "Command and options described by the semantic contract.",
        "required": True,
        "sensitivity": "public",
        "type": {"kind": "string"},
    }

    parsed = parse_caller_invocation(export, ["status"], stdin_requested=False)
    plan = compile_gateway_invocation(source, export, parsed)

    assert plan.argv == ("audit", "status")


def test_v4_raw_argv_rejected_when_no_authored_pattern_matches() -> None:
    _source, export = _v4_pattern_export(
        [
            {
                "name": "hashes",
                "min_positionals": 1,
                "allowed_flags": ["--json"],
            }
        ]
    )

    with pytest.raises(ProcessBindingError, match="does not match any declared pattern"):
        parse_caller_invocation(
            export, ["compute-hashes", "--bogus"], stdin_requested=False
        )


def test_v4_raw_argv_passthrough_does_not_activate_without_patterns() -> None:
    _source, export = _v4_pattern_export(None)

    with pytest.raises(ProcessBindingError, match="unknown option --json"):
        parse_caller_invocation(
            export, ["compute-hashes", "--json"], stdin_requested=False
        )


def test_authored_pattern_treats_unpatterned_flag_as_switch() -> None:
    pattern, name = select_authored_argv_pattern(
        [
            {
                "name": "switch",
                "min_positionals": 1,
                "allowed_flags": ["--verbose"],
                "positional_patterns": {"0": "^run$"},
            }
        ],
        ["--verbose", "run"],
        stdin_requested=False,
    )

    assert pattern["name"] == name == "switch"


def test_authored_pattern_rejects_duplicate_value_flag() -> None:
    with pytest.raises(ProcessBindingError, match="duplicate flag --date"):
        select_authored_argv_pattern(
            [
                {
                    "name": "date",
                    "min_positionals": 0,
                    "allowed_flags": ["--date"],
                    "flag_patterns": {"--date": "^safe$"},
                }
            ],
            ["--date", "safe", "--date", "unsafe"],
            stdin_requested=False,
        )


def test_authored_pattern_rejects_missing_value_flag_argument() -> None:
    with pytest.raises(ProcessBindingError, match="requires a value"):
        select_authored_argv_pattern(
            [
                {
                    "name": "output",
                    "allowed_flags": ["--output"],
                    "flag_patterns": {"--output": ".+"},
                }
            ],
            ["--output"],
            stdin_requested=False,
        )


def test_authored_pattern_rejects_extra_positionals_by_default() -> None:
    with pytest.raises(ProcessBindingError, match="does not match"):
        select_authored_argv_pattern(
            [{"name": "one", "min_positionals": 1}],
            ["first", "second"],
            stdin_requested=False,
        )


def test_authored_pattern_allows_extra_positionals_only_when_authored() -> None:
    pattern, _name = select_authored_argv_pattern(
        [
            {
                "name": "many",
                "min_positionals": 1,
                "allow_extra_positionals": True,
            }
        ],
        ["first", "second"],
        stdin_requested=False,
    )

    assert pattern["name"] == "many"


@pytest.mark.parametrize(
    "argv",
    [
        ["compute-hashes", "--json"],
        ["compute-hashes", "--output", "result.json"],
        ["compute-hashes", "--output=result.json"],
        ["compute-hashes", "--", "--literal"],
    ],
)
def test_v4_authored_argv_pattern_preserves_accepted_predecessor_cases(
    argv: list[str],
) -> None:
    patterns = [
        {
            "name": "hashes",
            "min_positionals": 1,
            "max_positionals": 2,
            "allow_stdin": False,
            "allowed_flags": ["--json", "--output"],
            "flag_patterns": {"--output": r".+\.json$"},
        }
    ]

    matching, name = select_authored_argv_pattern(
        patterns,
        argv,
        stdin_requested=False,
    )

    assert matching == patterns[0]
    assert name == "hashes"


@pytest.mark.parametrize(
    ("argv", "stdin_requested"),
    [
        (["compute-hashes"], True),
        (["compute-hashes", "--unknown"], False),
        (["compute-hashes", "--output", "result.txt"], False),
        (["--json"], False),
    ],
)
def test_v4_authored_argv_pattern_preserves_unmatched_predecessor_cases(
    argv: list[str],
    stdin_requested: bool,
) -> None:
    patterns = [
        {
            "name": "hashes",
            "min_positionals": 1,
            "max_positionals": 2,
            "allow_stdin": False,
            "allowed_flags": ["--json", "--output"],
            "flag_patterns": {"--output": r".+\.json$"},
        }
    ]

    with pytest.raises(
        ProcessBindingError,
        match="invocation does not match any declared pattern",
    ):
        select_authored_argv_pattern(
            patterns,
            argv,
            stdin_requested=stdin_requested,
        )


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["compute-hashes", "--json", "--json"], "duplicate flag --json"),
        (["compute-hashes", "--output"], "flag --output requires a value"),
        (["compute-hashes", "--json=true"], "switch --json does not take a value"),
    ],
)
def test_v4_authored_argv_pattern_preserves_predecessor_argv_errors(
    argv: list[str],
    message: str,
) -> None:
    patterns = [
        {
            "name": "hashes",
            "min_positionals": 1,
            "max_positionals": 2,
            "allowed_flags": ["--json", "--output"],
            "flag_patterns": {"--output": r".+\.json$"},
        }
    ]

    with pytest.raises(ProcessBindingError, match=message):
        select_authored_argv_pattern(
            patterns,
            argv,
            stdin_requested=False,
        )


def test_v4_authored_argv_pattern_preserves_predecessor_ambiguity_error() -> None:
    patterns = [
        {"name": "first", "min_positionals": 1},
        {"name": "second", "min_positionals": 1},
    ]

    with pytest.raises(ProcessBindingError, match="matches multiple patterns"):
        select_authored_argv_pattern(
            patterns,
            ["compute-hashes"],
            stdin_requested=False,
        )


@pytest.mark.parametrize(
    ("patterns", "message"),
    [
        ([], "at least one pattern"),
        ([{"name": "bad", "min_positionals": -1}], "min_positionals"),
        (
            [
                {
                    "name": "bad",
                    "min_positionals": 1,
                    "positional_patterns": {"nope": ".*"},
                }
            ],
            "numeric index",
        ),
    ],
)
def test_v4_authored_argv_pattern_preserves_predecessor_declaration_errors(
    patterns: list[dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(ProcessBindingError, match=message):
        select_authored_argv_pattern(
            patterns,
            ["compute-hashes"],
            stdin_requested=False,
        )


def test_v4_natural_language_interface_is_not_process_compilable() -> None:
    source, export = _v4_export()
    declaration = dict(export.declaration)
    declaration.pop("process_binding")
    natural = InterfaceExport(
        interface_id=export.interface_id,
        version=export.version,
        local_name=export.local_name,
        module_node_id=export.module_node_id,
        declaration=declaration,
        source_node_id=export.source_node_id,
        source_interface_id=export.source_interface_id,
        export_declaration=export.export_declaration,
    )

    with pytest.raises(ProcessBindingError, match="not process-bindable"):
        parse_caller_invocation(natural, [], stdin_requested=False)


def test_parse_and_compile_merges_fixed_and_public_bindings_deterministically() -> None:
    export = _export(
        {
            "targets": _argument(
                {"kind": "positional", "position": 1, "arity": {"minimum": 1, "maximum": None}},
                {"kind": "list", "element_type": {"kind": "string"}},
            ),
            "count": _argument(
                {"kind": "option", "name": "--count", "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "integer", "minimum": 1, "maximum": 4},
            ),
            "verbose": _argument(
                {"kind": "switch", "name": "--verbose"},
                {"kind": "flag"},
                required=False,
                default=False,
            ),
        },
        fixed=[
            {"kind": "positional", "position": 0, "value": "run", "type": {"kind": "string"}},
            {"kind": "option", "name": "--format", "value": "json", "type": {"kind": "string"}},
        ],
    )

    parsed = parse_caller_invocation(
        export,
        ["one", "two", "--verbose", "--count", "2"],
        stdin_requested=False,
    )
    plan = compile_gateway_invocation(_source(), export, parsed)

    assert parsed.values == {"count": 2, "targets": ["one", "two"], "verbose": True}
    assert plan.argv == (
        "run",
        "one",
        "two",
        "--count",
        "2",
        "--verbose",
        "--format",
        "json",
    )
    assert plan.stdin_argument_id is None


def test_defaults_and_stdin_are_kept_out_of_argv() -> None:
    export = _export(
        {
            "mode": _argument(
                {"kind": "option", "name": "--mode", "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "enum", "values": [{"value": "safe", "description": "Safe."}]},
                required=False,
                default="safe",
            ),
            "payload": _argument(
                {"kind": "stdin", "encoding": "utf-8", "framing": "raw"},
                {"kind": "string"},
            ),
        }
    )

    parsed = parse_caller_invocation(export, [], stdin_requested=True)
    plan = compile_gateway_invocation(_source(), export, parsed)

    assert parsed.values == {"mode": "safe"}
    assert plan.argv == ("--mode", "safe")
    assert plan.stdin_argument_id == "payload"


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "missing required argument"),
        (["4"], "above maximum"),
        (["not-an-int"], "expected integer"),
        (["2", "extra"], "unexpected trailing value"),
        (["2", "--unknown"], "unknown option"),
    ],
)
def test_parse_rejects_missing_extra_unknown_and_invalid_values(
    argv: list[str], message: str
) -> None:
    export = _export(
        {
            "count": _argument(
                {"kind": "positional", "position": 0, "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "integer", "minimum": 1, "maximum": 3},
            )
        }
    )
    with pytest.raises(ProcessBindingError, match=message):
        parse_caller_invocation(export, argv, stdin_requested=False)


def test_binding_rejects_collisions_reserved_names_and_secret_argv() -> None:
    collision = _export(
        {
            "value": _argument(
                {"kind": "positional", "position": 0, "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "string"},
            )
        },
        fixed=[{"kind": "positional", "position": 0, "value": "fixed", "type": {"kind": "string"}}],
    )
    with pytest.raises(ProcessBindingError, match="position 0 collision"):
        parse_caller_invocation(collision, ["caller"], stdin_requested=False)

    reserved = _export(
        {
            "value": _argument(
                {"kind": "option", "name": "--dry-run", "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "string"},
            )
        }
    )
    with pytest.raises(ProcessBindingError, match="dispatcher-owned option"):
        parse_caller_invocation(reserved, ["--dry-run", "x"], stdin_requested=False)

    secret = _export(
        {
            "token": _argument(
                {"kind": "option", "name": "--token", "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "string"},
                sensitivity="secret",
            )
        }
    )
    with pytest.raises(ProcessBindingError, match="secret.*stdin"):
        parse_caller_invocation(secret, ["--token", "hidden"], stdin_requested=False)


def test_unbounded_positional_stops_at_declared_option_and_double_dash_is_positional() -> None:
    export = _export(
        {
            "items": _argument(
                {"kind": "positional", "position": 0, "arity": {"minimum": 1, "maximum": None}},
                {"kind": "list", "element_type": {"kind": "string"}},
            ),
            "mode": _argument(
                {"kind": "option", "name": "--mode", "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "string"},
                required=False,
                default="normal",
            ),
        }
    )

    parsed = parse_caller_invocation(
        export, ["one", "--mode", "fast"], stdin_requested=False
    )
    assert parsed.values == {"items": ["one"], "mode": "fast"}

    parsed = parse_caller_invocation(
        export, ["one", "--", "--mode"], stdin_requested=False
    )
    assert parsed.values == {"items": ["one", "--mode"], "mode": "normal"}


def test_stdin_request_requires_exactly_one_stdin_argument() -> None:
    export = _export({})
    with pytest.raises(ProcessBindingError, match="does not declare stdin"):
        parse_caller_invocation(export, [], stdin_requested=True)

    export = _export(
        {
            "payload": _argument(
                {"kind": "stdin", "encoding": "utf-8", "framing": "raw"},
                {"kind": "string"},
            )
        }
    )
    with pytest.raises(ProcessBindingError, match="requires --stdin"):
        parse_caller_invocation(export, [], stdin_requested=False)
