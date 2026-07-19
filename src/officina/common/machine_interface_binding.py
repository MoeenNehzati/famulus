"""Pure parser and argv compiler for nested machine-interface exports."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
import uuid

import yaml

from .blueprint_graph import BlueprintNode, MachineInterfaceExport


class MachineInterfaceBindingError(ValueError):
    """Raised when an export binding or caller invocation is ambiguous or invalid."""


@dataclass(frozen=True)
class ParsedCallerInvocation:
    values: Mapping[str, object]
    stdin_requested: bool


@dataclass(frozen=True)
class CompiledInvocationPlan:
    argv: tuple[str, ...]
    stdin_argument_id: str | None


_DISPATCHER_OPTIONS = frozenset({"--caller-skill", "--dry-run", "--stdin"})
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _arguments(export: MachineInterfaceExport) -> dict[str, dict[str, Any]]:
    contract = export.declaration.get("contract")
    raw = contract.get("arguments") if isinstance(contract, Mapping) else None
    if not isinstance(raw, Mapping):
        raise MachineInterfaceBindingError(
            f"{export.interface_id}: contract.arguments must be a mapping"
        )
    result: dict[str, dict[str, Any]] = {}
    for argument_id, declaration in raw.items():
        if not isinstance(argument_id, str) or not isinstance(declaration, Mapping):
            raise MachineInterfaceBindingError(
                f"{export.interface_id}: every argument must be a named mapping"
            )
        result[argument_id] = dict(declaration)
    return result


def _fixed_bindings(export: MachineInterfaceExport) -> list[dict[str, Any]]:
    invocation = export.declaration.get("invocation_binding")
    raw = invocation.get("fixed") if isinstance(invocation, Mapping) else None
    if not isinstance(raw, list):
        raise MachineInterfaceBindingError(
            f"{export.interface_id}: invocation_binding.fixed must be a list"
        )
    if not all(isinstance(entry, Mapping) for entry in raw):
        raise MachineInterfaceBindingError(
            f"{export.interface_id}: every fixed binding must be a mapping"
        )
    return [dict(entry) for entry in raw]


def _arity(binding: Mapping[str, Any], context: str) -> tuple[int, int | None]:
    raw = binding.get("arity")
    if not isinstance(raw, Mapping):
        raise MachineInterfaceBindingError(f"{context}: arity must be a mapping")
    minimum = raw.get("minimum")
    maximum = raw.get("maximum")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 0:
        raise MachineInterfaceBindingError(f"{context}: invalid minimum arity")
    if maximum is not None and (
        not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < minimum
    ):
        raise MachineInterfaceBindingError(f"{context}: invalid maximum arity")
    return minimum, maximum


def _contains_secret(type_spec: object) -> bool:
    if not isinstance(type_spec, Mapping):
        return False
    if type_spec.get("sensitivity") in {"secret", "credential"}:
        return True
    return any(
        _contains_secret(type_spec.get(field))
        for field in ("element_type", "content_type", "entry_type")
    )


def _validate_layout(export: MachineInterfaceExport) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    arguments = _arguments(export)
    fixed = _fixed_bindings(export)
    positions: dict[int, str] = {}
    names: dict[str, str] = {}
    unbounded_positions: list[int] = []

    def register(binding: Mapping[str, Any], owner: str, *, caller: bool) -> None:
        kind = binding.get("kind")
        if kind == "positional":
            position = binding.get("position")
            if not isinstance(position, int) or isinstance(position, bool) or position < 0:
                raise MachineInterfaceBindingError(f"{owner}: invalid positional position")
            previous = positions.get(position)
            if previous is not None:
                raise MachineInterfaceBindingError(
                    f"position {position} collision between {previous} and {owner}"
                )
            positions[position] = owner
            if caller:
                _minimum, maximum = _arity(binding, owner)
                if maximum is None:
                    unbounded_positions.append(position)
            return
        if kind in {"option", "switch"}:
            name = binding.get("name")
            if not isinstance(name, str) or not name.startswith("--"):
                raise MachineInterfaceBindingError(f"{owner}: invalid named binding")
            if name in _DISPATCHER_OPTIONS:
                raise MachineInterfaceBindingError(
                    f"{owner}: {name} is a dispatcher-owned option"
                )
            previous = names.get(name)
            if previous is not None:
                raise MachineInterfaceBindingError(
                    f"option {name} collision between {previous} and {owner}"
                )
            names[name] = owner
            if kind == "option" and caller:
                _arity(binding, owner)
            return
        if kind == "stdin" and caller:
            return
        raise MachineInterfaceBindingError(f"{owner}: unsupported binding kind {kind!r}")

    for index, binding in enumerate(fixed):
        owner = f"fixed[{index}]"
        register(binding, owner, caller=False)
        type_spec = binding.get("type")
        if binding.get("kind") != "switch":
            if _contains_secret(type_spec):
                raise MachineInterfaceBindingError(f"{owner}: secret fixed values are forbidden")
            _validate_typed_value(binding.get("value"), type_spec, owner)

    stdin_ids: list[str] = []
    for argument_id, declaration in arguments.items():
        binding = declaration.get("invocation_binding")
        if not isinstance(binding, Mapping):
            raise MachineInterfaceBindingError(
                f"{argument_id}: invocation_binding must be a mapping"
            )
        register(binding, argument_id, caller=True)
        if binding.get("kind") == "stdin":
            stdin_ids.append(argument_id)
        elif declaration.get("sensitivity") in {"secret", "credential"} or _contains_secret(
            declaration.get("type")
        ):
            raise MachineInterfaceBindingError(
                f"{argument_id}: secret or credential values must use stdin"
            )
    if len(stdin_ids) > 1:
        raise MachineInterfaceBindingError(
            f"{export.interface_id}: exactly zero or one stdin argument is allowed"
        )
    if unbounded_positions and max(positions) > min(unbounded_positions):
        raise MachineInterfaceBindingError(
            f"{export.interface_id}: unbounded positional must be the final positional binding"
        )
    return arguments, fixed


def _validate_format(value: str, format_spec: object, context: str) -> None:
    if not isinstance(format_spec, Mapping):
        return
    if "named" in format_spec:
        named = format_spec["named"]
        valid = True
        if named == "identifier":
            valid = bool(_IDENTIFIER.fullmatch(value))
        elif named == "json":
            try:
                json.loads(value)
            except json.JSONDecodeError:
                valid = False
        elif named == "yaml":
            try:
                yaml.safe_load(value)
            except yaml.YAMLError:
                valid = False
        elif named == "email":
            valid = bool(_EMAIL.fullmatch(value))
        elif named == "uri":
            parsed = urlparse(value)
            valid = bool(parsed.scheme)
        elif named == "uuid":
            try:
                uuid.UUID(value)
            except ValueError:
                valid = False
        if not valid:
            raise MachineInterfaceBindingError(f"{context}: invalid {named} value")
    elif "regex" in format_spec:
        regex = format_spec["regex"]
        if not isinstance(regex, Mapping) or not isinstance(regex.get("pattern"), str):
            raise MachineInterfaceBindingError(f"{context}: invalid regex format")
        matcher = re.fullmatch if regex.get("matching") == "full" else re.match
        if matcher(regex["pattern"], value) is None:
            raise MachineInterfaceBindingError(f"{context}: value does not match format")
    elif "template" in format_spec:
        template = format_spec["template"]
        if not isinstance(template, str) or "{value}" not in template:
            raise MachineInterfaceBindingError(f"{context}: unsupported format template")


def _validate_typed_value(value: object, type_spec: object, context: str) -> object:
    if not isinstance(type_spec, Mapping):
        raise MachineInterfaceBindingError(f"{context}: type must be a mapping")
    kind = type_spec.get("kind")
    if kind == "list":
        if not isinstance(value, (list, tuple)):
            raise MachineInterfaceBindingError(f"{context}: expected list")
        return [
            _validate_typed_value(item, type_spec.get("element_type"), context)
            for item in value
        ]
    if kind == "integer":
        try:
            parsed: object = int(value) if not isinstance(value, bool) else None
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, int):
            raise MachineInterfaceBindingError(f"{context}: expected integer")
    elif kind == "number":
        try:
            parsed = float(value) if not isinstance(value, bool) else None
        except (TypeError, ValueError):
            parsed = None
        if not isinstance(parsed, float):
            raise MachineInterfaceBindingError(f"{context}: expected number")
    elif kind == "boolean":
        if isinstance(value, bool):
            parsed = value
        elif isinstance(value, str) and value.lower() in {"true", "false"}:
            parsed = value.lower() == "true"
        else:
            raise MachineInterfaceBindingError(f"{context}: expected boolean")
    elif kind == "flag":
        if not isinstance(value, bool):
            raise MachineInterfaceBindingError(f"{context}: expected flag")
        parsed = value
    elif kind == "enum":
        values = type_spec.get("values")
        if not isinstance(values, list):
            parsed = value
        else:
            choices = [entry.get("value") for entry in values if isinstance(entry, Mapping)]
            parsed = next((choice for choice in choices if str(choice) == str(value)), None)
            if parsed is None:
                raise MachineInterfaceBindingError(f"{context}: value is not in enum")
    elif kind in {"string", "path", "file", "dir", "date", "datetime", "duration"}:
        if not isinstance(value, str):
            raise MachineInterfaceBindingError(f"{context}: expected string")
        parsed = value
        _validate_format(value, type_spec.get("format"), context)
        if kind in {"date", "datetime"}:
            formats = type_spec.get("date_formats", [])
            if isinstance(formats, list) and formats:
                if not any(_matches_date(value, candidate) for candidate in formats):
                    raise MachineInterfaceBindingError(f"{context}: invalid {kind} value")
    else:
        raise MachineInterfaceBindingError(f"{context}: unsupported terminal type {kind!r}")

    if kind in {"integer", "number"}:
        minimum = type_spec.get("minimum")
        maximum = type_spec.get("maximum")
        if isinstance(minimum, (int, float)) and parsed < minimum:  # type: ignore[operator]
            raise MachineInterfaceBindingError(f"{context}: below minimum {minimum}")
        if isinstance(maximum, (int, float)) and parsed > maximum:  # type: ignore[operator]
            raise MachineInterfaceBindingError(f"{context}: above maximum {maximum}")
    return parsed


def _matches_date(value: str, format_name: object) -> bool:
    try:
        if format_name == "iso-8601":
            dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif isinstance(format_name, str):
            dt.datetime.strptime(value, format_name)
        else:
            return False
    except ValueError:
        return False
    return True


def _caller_tokens(argv: Sequence[str], known_names: set[str], fixed_names: set[str]) -> tuple[list[str], list[str]]:
    tokens = list(argv)
    marker = tokens.index("--") if "--" in tokens else None
    forced = tokens[marker + 1 :] if marker is not None else []
    active = tokens[:marker] if marker is not None else tokens
    split = len(active)
    for index, token in enumerate(active):
        if token.startswith("--"):
            split = index
            break
    positionals = active[:split] + forced
    named = active[split:]
    for token in named:
        if token in fixed_names:
            raise MachineInterfaceBindingError(f"caller cannot override fixed option {token}")
        if token.startswith("--") and token not in known_names:
            raise MachineInterfaceBindingError(f"unknown option {token}")
    return positionals, named


def parse_caller_invocation(
    export: MachineInterfaceExport,
    argv: Sequence[str],
    *,
    stdin_requested: bool,
) -> ParsedCallerInvocation:
    """Parse only the caller-visible argument tail for one export."""

    arguments, fixed = _validate_layout(export)
    positional = sorted(
        (
            (declaration["invocation_binding"]["position"], argument_id, declaration)
            for argument_id, declaration in arguments.items()
            if declaration["invocation_binding"].get("kind") == "positional"
        ),
        key=lambda item: item[0],
    )
    named_by_name = {
        declaration["invocation_binding"]["name"]: (argument_id, declaration)
        for argument_id, declaration in arguments.items()
        if declaration["invocation_binding"].get("kind") in {"option", "switch"}
    }
    fixed_names = {
        binding["name"]
        for binding in fixed
        if binding.get("kind") in {"option", "switch"}
    }
    positional_tokens, named_tokens = _caller_tokens(
        argv, set(named_by_name), fixed_names
    )
    values: dict[str, object] = {}
    offset = 0
    for index, (_position, argument_id, declaration) in enumerate(positional):
        binding = declaration["invocation_binding"]
        minimum, maximum = _arity(binding, argument_id)
        future_minimum = sum(
            _arity(other[2]["invocation_binding"], other[1])[0]
            for other in positional[index + 1 :]
        )
        available = len(positional_tokens) - offset
        upper = available - future_minimum
        take = upper if maximum is None else min(maximum, upper)
        if take < minimum:
            take = max(take, 0)
        raw_values = positional_tokens[offset : offset + take]
        offset += take
        if len(raw_values) < minimum:
            continue
        type_spec = declaration.get("type")
        if type_spec.get("kind") == "list" if isinstance(type_spec, Mapping) else False:
            values[argument_id] = _validate_typed_value(raw_values, type_spec, argument_id)
        elif maximum != 1 or minimum != 1:
            values[argument_id] = [
                _validate_typed_value(item, type_spec, argument_id) for item in raw_values
            ]
        elif raw_values:
            values[argument_id] = _validate_typed_value(
                raw_values[0], type_spec, argument_id
            )
    if offset < len(positional_tokens):
        raise MachineInterfaceBindingError(
            f"unexpected trailing value {positional_tokens[offset]!r}"
        )

    index = 0
    seen_names: set[str] = set()
    while index < len(named_tokens):
        name = named_tokens[index]
        if name not in named_by_name:
            if name.startswith("--"):
                raise MachineInterfaceBindingError(f"unknown option {name}")
            raise MachineInterfaceBindingError(f"unexpected trailing value {name!r}")
        if name in seen_names:
            raise MachineInterfaceBindingError(f"option {name} supplied more than once")
        seen_names.add(name)
        argument_id, declaration = named_by_name[name]
        binding = declaration["invocation_binding"]
        if binding["kind"] == "switch":
            values[argument_id] = True
            index += 1
            continue
        minimum, maximum = _arity(binding, argument_id)
        cursor = index + 1
        raw_values: list[str] = []
        while cursor < len(named_tokens) and named_tokens[cursor] not in named_by_name:
            if named_tokens[cursor].startswith("--"):
                raise MachineInterfaceBindingError(f"unknown option {named_tokens[cursor]}")
            if maximum is not None and len(raw_values) >= maximum:
                break
            raw_values.append(named_tokens[cursor])
            cursor += 1
        if len(raw_values) < minimum:
            raise MachineInterfaceBindingError(f"{argument_id}: missing option value")
        type_spec = declaration.get("type")
        if isinstance(type_spec, Mapping) and type_spec.get("kind") == "list":
            values[argument_id] = _validate_typed_value(raw_values, type_spec, argument_id)
        elif maximum != 1 or minimum != 1:
            values[argument_id] = [
                _validate_typed_value(item, type_spec, argument_id) for item in raw_values
            ]
        else:
            values[argument_id] = _validate_typed_value(
                raw_values[0], type_spec, argument_id
            )
        index = cursor

    stdin_arguments = [
        argument_id
        for argument_id, declaration in arguments.items()
        if declaration["invocation_binding"].get("kind") == "stdin"
    ]
    if stdin_requested and not stdin_arguments:
        raise MachineInterfaceBindingError(
            f"{export.interface_id} does not declare stdin"
        )
    if stdin_arguments and arguments[stdin_arguments[0]].get("required") is True and not stdin_requested:
        raise MachineInterfaceBindingError(
            f"{stdin_arguments[0]} requires --stdin"
        )

    for argument_id, declaration in sorted(arguments.items()):
        if declaration["invocation_binding"].get("kind") == "stdin":
            continue
        if argument_id in values:
            continue
        if "default" in declaration:
            values[argument_id] = _validate_typed_value(
                declaration["default"], declaration.get("type"), argument_id
            )
        elif declaration.get("invocation_binding", {}).get("kind") == "switch":
            values[argument_id] = False
        elif declaration.get("required") is True:
            raise MachineInterfaceBindingError(
                f"missing required argument {argument_id}"
            )
    return ParsedCallerInvocation(dict(sorted(values.items())), stdin_requested)


def _encode(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _value_tokens(value: object) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [_encode(item) for item in value]
    return [_encode(value)]


def compile_gateway_invocation(
    module: BlueprintNode,
    export: MachineInterfaceExport,
    parsed: ParsedCallerInvocation,
) -> CompiledInvocationPlan:
    """Compile a parsed call to deterministic argv without gateway prefixes."""

    if module.node_type != "machine-module" or module.node_id != export.module_node_id:
        raise MachineInterfaceBindingError(
            f"{export.interface_id}: owning machine module does not match"
        )
    arguments, fixed = _validate_layout(export)
    positionals: list[tuple[int, list[str]]] = []
    fixed_named: list[tuple[str, list[str]]] = []
    for binding in fixed:
        kind = binding["kind"]
        if kind == "positional":
            positionals.append((binding["position"], _value_tokens(binding["value"])))
        elif kind == "option":
            fixed_named.append((binding["name"], [binding["name"], *_value_tokens(binding["value"])]))
        else:
            fixed_named.append((binding["name"], [binding["name"]]))

    caller_named: list[tuple[str, list[str]]] = []
    stdin_argument_id: str | None = None
    for argument_id, declaration in arguments.items():
        binding = declaration["invocation_binding"]
        kind = binding["kind"]
        if kind == "stdin":
            if parsed.stdin_requested:
                stdin_argument_id = argument_id
            continue
        if argument_id not in parsed.values:
            continue
        value = parsed.values[argument_id]
        if kind == "positional":
            positionals.append((binding["position"], _value_tokens(value)))
        elif kind == "option":
            caller_named.append(
                (argument_id, [binding["name"], *_value_tokens(value)])
            )
        elif value is True:
            caller_named.append((argument_id, [binding["name"]]))

    argv: list[str] = []
    for _position, tokens in sorted(positionals, key=lambda item: item[0]):
        argv.extend(tokens)
    for _argument_id, tokens in sorted(caller_named, key=lambda item: item[0]):
        argv.extend(tokens)
    for _name, tokens in sorted(fixed_named, key=lambda item: item[0]):
        argv.extend(tokens)
    return CompiledInvocationPlan(tuple(argv), stdin_argument_id)
