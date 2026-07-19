from __future__ import annotations

from pathlib import Path

import pytest

from officina.common.blueprint_graph import BlueprintNode, MachineInterfaceExport
from officina.common.machine_interface_binding import (
    MachineInterfaceBindingError,
    compile_gateway_invocation,
    parse_caller_invocation,
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


def _export(arguments: dict[str, dict[str, object]], fixed: list[dict[str, object]] | None = None) -> MachineInterfaceExport:
    declaration = {
        "id": "demo-skill.machine.run",
        "version": 1,
        "invocation_binding": {"fixed": fixed or []},
        "contract": {"arguments": arguments},
    }
    return MachineInterfaceExport(
        interface_id="demo-skill.machine.run",
        version=1,
        local_name="run",
        module_node_id="demo-skill.machine-module.worker",
        declaration=declaration,
    )


def _module() -> BlueprintNode:
    root = Path("/repo/skills/demo-skill")
    return BlueprintNode(
        node_id="demo-skill.machine-module.worker",
        node_type="machine-module",
        version=1,
        skill_root=root,
        blueprint_path=root / "_rtx" / "._worker.py.blueprint.yaml",
        gateway_path=root / "_rtx" / "_worker.py",
        declaration={"gateway": {"kind": "python-entrypoint", "args_prefix": ["gateway"]}},
    )


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
    plan = compile_gateway_invocation(_module(), export, parsed)

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
    plan = compile_gateway_invocation(_module(), export, parsed)

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
    with pytest.raises(MachineInterfaceBindingError, match=message):
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
    with pytest.raises(MachineInterfaceBindingError, match="position 0 collision"):
        parse_caller_invocation(collision, ["caller"], stdin_requested=False)

    reserved = _export(
        {
            "value": _argument(
                {"kind": "option", "name": "--dry-run", "arity": {"minimum": 1, "maximum": 1}},
                {"kind": "string"},
            )
        }
    )
    with pytest.raises(MachineInterfaceBindingError, match="dispatcher-owned option"):
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
    with pytest.raises(MachineInterfaceBindingError, match="secret.*stdin"):
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
    with pytest.raises(MachineInterfaceBindingError, match="does not declare stdin"):
        parse_caller_invocation(export, [], stdin_requested=True)

    export = _export(
        {
            "payload": _argument(
                {"kind": "stdin", "encoding": "utf-8", "framing": "raw"},
                {"kind": "string"},
            )
        }
    )
    with pytest.raises(MachineInterfaceBindingError, match="requires --stdin"):
        parse_caller_invocation(export, [], stdin_requested=False)
