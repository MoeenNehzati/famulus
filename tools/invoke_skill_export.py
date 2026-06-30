#!/usr/bin/env python3
"""Validate and invoke a skill script interface declared in blueprint.yaml.

This dispatcher is the sanctioned boundary for local cross-skill script calls.
It resolves a declared script interface from the callee blueprint,
checks that the caller is allowed to use it, validates the invocation against
available patterns, and then executes the resulting command.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_ROOT = REPO_ROOT / "skills"


class InvocationError(Exception):
    """Raised when a dispatcher request is invalid."""


def load_blueprint(skill_name: str) -> dict[str, Any]:
    path = SKILLS_ROOT / skill_name / "blueprint.yaml"
    if not path.exists():
        raise InvocationError(f"skill `{skill_name}` does not define blueprint.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise InvocationError(f"{path}: top level must be a mapping")
    return raw


def expect_mapping(value: Any, context: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise InvocationError(f"{context}: expected mapping")
    return value


def expect_string_list(value: Any, context: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise InvocationError(f"{context}: expected list of non-empty strings")
    return value


def expect_list(value: Any, context: str) -> list[Any]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise InvocationError(f"{context}: expected list")
    return value


def parse_cli() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Invoke an exported skill script interface declared in blueprint.yaml.",
        epilog=(
            "Examples:\n"
            "  python3 tools/invoke_skill_export.py --dry-run --caller-skill daily-plan "
            "list-manager read-list /tmp/todo.yaml state=incomplete\n"
            "  python3 tools/invoke_skill_export.py --dry-run list-manager read-list /tmp/todo.yaml"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--caller-skill",
        help="Owning skill requesting the invocation. If omitted, only exported interfaces are allowed.",
    )
    parser.add_argument(
        "--stdin",
        action="store_true",
        help="Read stdin and forward it to the target command. Fails if no pattern allows stdin.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the resolved invocation as JSON instead of executing it.",
    )
    parser.add_argument("target_skill")
    parser.add_argument("script_interface")
    parser.add_argument("script_args", nargs=argparse.REMAINDER)
    return parser.parse_args()


def split_args(script_args: list[str]) -> tuple[list[str], list[str]]:
    """Split arguments into flags and positionals."""
    flags: list[str] = []
    positionals: list[str] = []
    i = 0
    while i < len(script_args):
        token = script_args[i]
        if token == "--":
            positionals.extend(script_args[i + 1 :])
            break
        if token.startswith("-") and token != "-":
            flag_name = token.split("=", 1)[0]
            flags.append(flag_name)
            # Track if this is a flag with a separate value argument
            if "=" not in token and i + 1 < len(script_args):
                next_token = script_args[i + 1]
                if next_token != "--" and not next_token.startswith("-"):
                    i += 1  # Skip the value in positional tracking
            i += 1
            continue
        positionals.append(token)
        i += 1
    return flags, positionals


def validate_pattern(
    pattern: dict[str, Any], script_args: list[str], stdin_requested: bool, pattern_name: str = ""
) -> bool:
    """Check if script_args matches this pattern. Returns True if it matches."""
    flags, positionals = split_args(script_args)
    provided_flag_set = set(flags)

    # Check stdin requirement
    allow_stdin = bool(pattern.get("allow_stdin", False))
    if stdin_requested and not allow_stdin:
        return False

    # Check positional counts
    min_positionals = pattern.get("min_positionals", 0)
    max_positionals = pattern.get("max_positionals")
    if not isinstance(min_positionals, int) or min_positionals < 0:
        raise InvocationError(f"pattern{' ' + pattern_name if pattern_name else ''}: min_positionals must be non-negative integer")
    if len(positionals) < min_positionals:
        return False
    if max_positionals is not None:
        if not isinstance(max_positionals, int) or max_positionals < min_positionals:
            raise InvocationError(f"pattern{' ' + pattern_name if pattern_name else ''}: max_positionals must be >= min_positionals")
        if len(positionals) > max_positionals:
            return False

    # Check required flags
    required_flags = set(expect_string_list(pattern.get("required_flags"), "required_flags"))
    if not required_flags.issubset(provided_flag_set):
        return False

    # Check forbidden flags
    forbidden_flags = set(expect_string_list(pattern.get("forbidden_flags"), "forbidden_flags"))
    if forbidden_flags & provided_flag_set:
        return False

    # Check allowed flags (if specified, only these are allowed)
    allowed_flags_raw = pattern.get("allowed_flags")
    if allowed_flags_raw is not None:
        allowed_flags = set(expect_string_list(allowed_flags_raw, "allowed_flags"))
        unexpected = provided_flag_set - allowed_flags
        if unexpected:
            return False

    # Check positional patterns (regex matching)
    positional_patterns = expect_mapping(pattern.get("positional_patterns"), "positional_patterns")
    for idx_str, regex_pattern in positional_patterns.items():
        try:
            idx = int(idx_str)
        except ValueError:
            raise InvocationError(f"positional_patterns key must be numeric index, got '{idx_str}'")
        if idx < 0 or idx >= len(positionals):
            return False
        if not re.match(regex_pattern, positionals[idx]):
            return False

    # Check flag value patterns (regex matching)
    flag_patterns = expect_mapping(pattern.get("flag_patterns"), "flag_patterns")
    for flag_name, regex_pattern in flag_patterns.items():
        if flag_name not in provided_flag_set:
            continue
        # Find the value for this flag in script_args
        flag_value = None
        for i, arg in enumerate(script_args):
            if arg == flag_name and i + 1 < len(script_args):
                flag_value = script_args[i + 1]
                break
            elif arg.startswith(flag_name + "="):
                flag_value = arg.split("=", 1)[1]
                break
        if flag_value is None:
            return False
        if not re.match(regex_pattern, flag_value):
            return False

    return True


def find_matching_pattern(
    interface_spec: dict[str, Any], script_args: list[str], stdin_requested: bool
) -> tuple[dict[str, Any], str]:
    """Find which pattern matches the invocation. Returns (pattern, pattern_name)."""
    patterns = expect_list(interface_spec.get("patterns"), "patterns")
    if not patterns:
        raise InvocationError("script interface must have at least one pattern")

    matching = None
    matching_name = ""
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            raise InvocationError(f"pattern {i} must be a mapping")
        pattern_name = pattern.get("name", f"pattern_{i}")
        if validate_pattern(pattern, script_args, stdin_requested, pattern_name):
            if matching is not None:
                raise InvocationError(f"invocation matches multiple patterns; ambiguous")
            matching = pattern
            matching_name = pattern_name
    if matching is None:
        raise InvocationError("invocation does not match any declared pattern")
    return matching, matching_name


def resolve_interface(
    target_skill: str,
    target_blueprint: dict[str, Any],
    caller_skill: str | None,
    script_interface: str,
    script_args: list[str],
    stdin_requested: bool,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Resolve and validate the interface. Returns (interface_spec, pattern_spec, pattern_name)."""
    interfaces = expect_mapping(target_blueprint.get("script_interfaces"), "script_interfaces")
    interface_spec = interfaces.get(script_interface)
    if not isinstance(interface_spec, dict):
        raise InvocationError(
            f"skill `{target_skill}` does not define script interface `{script_interface}`"
        )

    # Find matching pattern
    pattern_spec, pattern_name = find_matching_pattern(interface_spec, script_args, stdin_requested)

    # Check if caller is allowed to use this interface
    exported = interface_spec.get("exported", False)
    allowed_callers = expect_string_list(interface_spec.get("allowed_callers"), "allowed_callers")

    # Owner skill can always use the interface
    if caller_skill == target_skill:
        return interface_spec, pattern_spec, pattern_name

    # External caller: check if exported or in allowed_callers
    if not exported and not allowed_callers:
        # Not exported and no specific callers: internal-only
        raise InvocationError(
            f"interface `{script_interface}` of skill `{target_skill}` is internal-only"
        )

    if not exported and allowed_callers:
        # Restricted to specific callers: check if caller is in the list
        if caller_skill is None or caller_skill not in allowed_callers:
            raise InvocationError(
                f"skill `{caller_skill}` is not in allowed_callers for `{target_skill}:{script_interface}`"
            )
    elif exported and allowed_callers and caller_skill is not None and caller_skill not in allowed_callers:
        # Even though exported, further restricted to specific callers
        raise InvocationError(
            f"skill `{caller_skill}` is not in allowed_callers for `{target_skill}:{script_interface}`"
        )

    # Check dependency declaration if caller is specified
    if caller_skill is not None:
        caller_blueprint = load_blueprint(caller_skill)
        depends_on = expect_mapping(caller_blueprint.get("depends_on"), f"{caller_skill}.depends_on")
        dep_spec = depends_on.get(target_skill)
        if not isinstance(dep_spec, dict):
            raise InvocationError(
                f"caller skill `{caller_skill}` does not declare dependency on `{target_skill}`"
            )

        target_version = target_blueprint.get("interface_version")
        declared_version = dep_spec.get("major_version")
        if declared_version != target_version:
            raise InvocationError(
                f"caller skill `{caller_skill}` depends on `{target_skill}` version "
                f"{declared_version}, but target exports version {target_version}"
            )

        allowed_exports = expect_string_list(dep_spec.get("exports"), f"{caller_skill}.depends_on.{target_skill}.exports")
        if script_interface not in allowed_exports:
            raise InvocationError(
                f"caller skill `{caller_skill}` is not allowed to invoke `{target_skill}:{script_interface}`"
            )

    return interface_spec, pattern_spec, pattern_name


def resolve_cwd(target_skill: str, interface_spec: dict[str, Any]) -> Path:
    cwd_value = interface_spec.get("cwd", "skill_root")
    if cwd_value == "skill_root":
        return SKILLS_ROOT / target_skill
    if cwd_value == "repo_root":
        return REPO_ROOT
    raise InvocationError(f"unsupported cwd value `{cwd_value}`")


def build_command(interface_spec: dict[str, Any], script_args: list[str]) -> list[str]:
    command = interface_spec.get("command")
    if not isinstance(command, list) or not all(isinstance(token, str) and token for token in command):
        raise InvocationError("script interface command must be a non-empty string list")
    return [*command, *script_args]


def main() -> int:
    args = parse_cli()

    try:
        target_blueprint = load_blueprint(args.target_skill)
        interface_spec, pattern_spec, pattern_name = resolve_interface(
            args.target_skill,
            target_blueprint,
            args.caller_skill,
            args.script_interface,
            args.script_args,
            args.stdin,
        )
        cwd = resolve_cwd(args.target_skill, interface_spec)
        command = build_command(interface_spec, args.script_args)
    except InvocationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = {
        "caller_skill": args.caller_skill,
        "target_skill": args.target_skill,
        "script_interface": args.script_interface,
        "pattern": pattern_name,
        "cwd": str(cwd),
        "command": command,
        "stdin": args.stdin,
    }
    if args.dry_run:
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0

    input_bytes = None
    if args.stdin:
        input_bytes = sys.stdin.buffer.read()

    completed = subprocess.run(
        command,
        cwd=cwd,
        input=input_bytes,
        capture_output=True,
    )
    if completed.stdout:
        sys.stdout.buffer.write(completed.stdout)
    if completed.stderr:
        sys.stderr.buffer.write(completed.stderr)
    return completed.returncode


if __name__ == "__main__":
    sys.exit(main())
