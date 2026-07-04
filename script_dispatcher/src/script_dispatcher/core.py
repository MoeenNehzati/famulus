"""Shared resolution and execution logic for skill script interfaces."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


LEGACY_DEFAULT_FIELDS = ("patterns", "allow_all_skills", "allowed_callers")


class InvocationError(Exception):
    """Raised when a dispatcher request is invalid."""


@dataclass(frozen=True)
class ResolvedInvocation:
    """Concrete invocation selected from a skill blueprint."""

    caller_skill: str
    target_skill: str
    script_interface: str
    pattern: str
    cwd: Path
    command: list[str]
    stdin: bool

    def as_payload(self) -> dict[str, Any]:
        return {
            "caller_skill": self.caller_skill,
            "target_skill": self.target_skill,
            "script_interface": self.script_interface,
            "pattern": self.pattern,
            "cwd": str(self.cwd),
            "command": list(self.command),
            "stdin": self.stdin,
        }


def get_repo_root(repo_root: Path | None = None) -> Path:
    """Resolve the AI repo root, preferring the installer-managed AI env var."""
    if repo_root is not None:
        return repo_root.resolve()

    env_root = os.environ.get("AI")
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if (candidate / "skills").is_dir() and (candidate / "scripts").is_dir():
            return candidate

    return Path(__file__).resolve().parents[3]


def skills_root(repo_root: Path | None = None) -> Path:
    return get_repo_root(repo_root) / "skills"


def load_blueprint(skill_name: str, repo_root: Path | None = None) -> dict[str, Any]:
    path = skills_root(repo_root) / skill_name / "blueprint.yaml"
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


def interface_id(interface_name: str, interface_spec: dict[str, Any], context: str) -> str:
    value = interface_spec.get("id")
    if not isinstance(value, str) or not value.strip():
        raise InvocationError(f"{context}: missing non-empty string `id`")
    return value.strip()


def legacy_default_fields(interface_spec: dict[str, Any]) -> dict[str, Any]:
    return {
        field: interface_spec[field]
        for field in LEGACY_DEFAULT_FIELDS
        if field in interface_spec
    }


def default_subinterface(interface_spec: dict[str, Any], context: str) -> dict[str, Any]:
    explicit = interface_spec.get("default")
    if explicit is not None:
        if not isinstance(explicit, dict):
            raise InvocationError(f"{context}.default: expected mapping")
        return explicit
    return legacy_default_fields(interface_spec)


def named_subinterfaces(interface_spec: dict[str, Any], context: str) -> dict[str, dict[str, Any]]:
    raw = interface_spec.get("subinterfaces")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise InvocationError(f"{context}.subinterfaces: expected mapping")
    result: dict[str, dict[str, Any]] = {}
    for name, spec in raw.items():
        if not isinstance(spec, dict):
            raise InvocationError(f"{context}.subinterfaces.{name}: expected mapping")
        result[name] = spec
    return result


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
            if "=" not in token and i + 1 < len(script_args):
                next_token = script_args[i + 1]
                if next_token != "--" and not next_token.startswith("-"):
                    i += 1
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

    allow_stdin = bool(pattern.get("allow_stdin", False))
    if stdin_requested and not allow_stdin:
        return False

    min_positionals = pattern.get("min_positionals", 0)
    max_positionals = pattern.get("max_positionals")
    if not isinstance(min_positionals, int) or min_positionals < 0:
        raise InvocationError(
            f"pattern{' ' + pattern_name if pattern_name else ''}: min_positionals must be non-negative integer"
        )
    if len(positionals) < min_positionals:
        return False
    if max_positionals is not None:
        if not isinstance(max_positionals, int) or max_positionals < min_positionals:
            raise InvocationError(
                f"pattern{' ' + pattern_name if pattern_name else ''}: max_positionals must be >= min_positionals"
            )
        if len(positionals) > max_positionals:
            return False

    required_flags = set(expect_string_list(pattern.get("required_flags"), "required_flags"))
    if not required_flags.issubset(provided_flag_set):
        return False

    forbidden_flags = set(expect_string_list(pattern.get("forbidden_flags"), "forbidden_flags"))
    if forbidden_flags & provided_flag_set:
        return False

    allowed_flags_raw = pattern.get("allowed_flags")
    if allowed_flags_raw is not None:
        allowed_flags = set(expect_string_list(allowed_flags_raw, "allowed_flags"))
        unexpected = provided_flag_set - allowed_flags
        if unexpected:
            return False

    positional_patterns = expect_mapping(pattern.get("positional_patterns"), "positional_patterns")
    for idx_str, regex_pattern in positional_patterns.items():
        try:
            idx = int(idx_str)
        except ValueError as exc:
            raise InvocationError(f"positional_patterns key must be numeric index, got '{idx_str}'") from exc
        if idx < 0 or idx >= len(positionals):
            return False
        if not re.match(regex_pattern, positionals[idx]):
            return False

    flag_patterns = expect_mapping(pattern.get("flag_patterns"), "flag_patterns")
    for flag_name, regex_pattern in flag_patterns.items():
        if flag_name not in provided_flag_set:
            continue
        flag_value = None
        for i, arg in enumerate(script_args):
            if arg == flag_name and i + 1 < len(script_args):
                flag_value = script_args[i + 1]
                break
            if arg.startswith(flag_name + "="):
                flag_value = arg.split("=", 1)[1]
                break
        if flag_value is None:
            return False
        if not re.match(regex_pattern, flag_value):
            return False

    return True


def find_matching_pattern(
    surface_spec: dict[str, Any], script_args: list[str], stdin_requested: bool
) -> tuple[dict[str, Any], str]:
    """Find which pattern matches the invocation. Returns (pattern, pattern_name)."""
    patterns_raw = surface_spec.get("patterns")
    if patterns_raw is None:
        return {}, "unrestricted"

    patterns = expect_list(patterns_raw, "patterns")
    if not patterns:
        raise InvocationError("script interface must have at least one pattern when `patterns` is declared")

    matching = None
    matching_name = ""
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            raise InvocationError(f"pattern {i} must be a mapping")
        pattern_name = pattern.get("name", f"pattern_{i}")
        if validate_pattern(pattern, script_args, stdin_requested, pattern_name):
            if matching is not None:
                raise InvocationError("invocation matches multiple patterns; ambiguous")
            matching = pattern
            matching_name = pattern_name
    if matching is None:
        raise InvocationError("invocation does not match any declared pattern")
    return matching, matching_name


def resolve_interface_surface(
    target_blueprint: dict[str, Any],
    interface_id_value: str,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Resolve an interface id to (parent interface spec, surface spec, resolved id)."""
    interfaces = expect_mapping(target_blueprint.get("script_interfaces"), "script_interfaces")
    for interface_name, interface_spec in interfaces.items():
        context = f"script_interfaces.{interface_name}"
        if not isinstance(interface_spec, dict):
            raise InvocationError(f"{context}: expected mapping")

        parent_id = interface_id(interface_name, interface_spec, context)
        if parent_id == interface_id_value:
            return interface_spec, default_subinterface(interface_spec, context), parent_id

        for sub_name, sub_spec in named_subinterfaces(interface_spec, context).items():
            sub_id = sub_spec.get("id")
            if not isinstance(sub_id, str) or not sub_id.strip():
                raise InvocationError(f"{context}.subinterfaces.{sub_name}: missing non-empty string `id`")
            if sub_id.strip() == interface_id_value:
                return interface_spec, sub_spec, sub_id.strip()

    raise InvocationError(f"skill does not define script interface id `{interface_id_value}`")


def resolve_interface(
    target_skill: str,
    target_blueprint: dict[str, Any],
    caller_skill: str,
    script_interface: str,
    script_args: list[str],
    stdin_requested: bool,
    repo_root: Path | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], str]:
    """Resolve and validate the interface.

    Returns (parent_interface_spec, surface_spec, pattern_spec, pattern_name).
    """
    parent_spec, surface_spec, resolved_id = resolve_interface_surface(target_blueprint, script_interface)
    pattern_spec, pattern_name = find_matching_pattern(surface_spec, script_args, stdin_requested)

    allow_all_skills = surface_spec.get("allow_all_skills", False)
    allowed_callers = expect_string_list(surface_spec.get("allowed_callers"), "allowed_callers")

    if caller_skill == target_skill:
        return parent_spec, surface_spec, pattern_spec, pattern_name

    if not allow_all_skills and not allowed_callers:
        raise InvocationError(
            f"interface `{resolved_id}` of skill `{target_skill}` is internal-only"
        )

    if not allow_all_skills and caller_skill not in allowed_callers:
        raise InvocationError(
            f"skill `{caller_skill}` is not in allowed_callers for `{target_skill}:{resolved_id}`"
        )

    caller_blueprint = load_blueprint(caller_skill, repo_root=repo_root)
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

    allowed_exports = expect_string_list(
        dep_spec.get("exports"),
        f"{caller_skill}.depends_on.{target_skill}.exports",
    )
    if resolved_id not in allowed_exports:
        raise InvocationError(
            f"caller skill `{caller_skill}` is not allowed to invoke `{target_skill}:{resolved_id}`"
        )

    return parent_spec, surface_spec, pattern_spec, pattern_name


def resolve_cwd(target_skill: str, parent_spec: dict[str, Any], repo_root: Path | None = None) -> Path:
    root = get_repo_root(repo_root)
    cwd_value = parent_spec.get("cwd", "skill_root")
    if cwd_value == "skill_root":
        return root / "skills" / target_skill
    if cwd_value == "repo_root":
        return root
    raise InvocationError(f"unsupported cwd value `{cwd_value}`")


def build_command(parent_spec: dict[str, Any], script_args: list[str]) -> list[str]:
    command = parent_spec.get("command")
    if not isinstance(command, list) or not all(isinstance(token, str) and token for token in command):
        raise InvocationError("script interface command must be a non-empty string list")
    return [*command, *script_args]


def resolve_dispatch(
    *,
    caller_skill: str,
    target_skill: str,
    script_interface: str,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
) -> ResolvedInvocation:
    args = args or []
    if not caller_skill.strip():
        raise InvocationError("caller_skill must be a non-empty string")
    target_blueprint = load_blueprint(target_skill, repo_root=repo_root)
    parent_spec, _surface_spec, _pattern_spec, pattern_name = resolve_interface(
        target_skill,
        target_blueprint,
        caller_skill.strip(),
        script_interface,
        args,
        stdin_requested,
        repo_root=repo_root,
    )
    cwd = resolve_cwd(target_skill, parent_spec, repo_root=repo_root)
    command = build_command(parent_spec, args)
    return ResolvedInvocation(
        caller_skill=caller_skill.strip(),
        target_skill=target_skill,
        script_interface=script_interface,
        pattern=pattern_name,
        cwd=cwd,
        command=command,
        stdin=stdin_requested,
    )


def dispatch(
    *,
    caller_skill: str,
    target_skill: str,
    script_interface: str,
    args: list[str] | None = None,
    stdin: str | bytes | None = None,
    timeout: float | None = None,
    capture_output: bool = True,
    check: bool = False,
    text: bool | None = None,
    repo_root: Path | None = None,
) -> subprocess.CompletedProcess[Any]:
    """Resolve and execute a declared skill interface."""
    resolved = resolve_dispatch(
        caller_skill=caller_skill,
        target_skill=target_skill,
        script_interface=script_interface,
        args=args or [],
        stdin_requested=stdin is not None,
        repo_root=repo_root,
    )

    run_kwargs: dict[str, Any] = {
        "cwd": resolved.cwd,
        "capture_output": capture_output,
        "check": check,
    }
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    if stdin is not None:
        run_kwargs["input"] = stdin
    if text is not None:
        run_kwargs["text"] = text
    elif isinstance(stdin, str):
        run_kwargs["text"] = True

    return subprocess.run(resolved.command, **run_kwargs)
