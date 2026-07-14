"""Shared resolution and execution logic for skill dispatcher interfaces."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import jsonschema
import yaml

from officina.common.blueprint_graph import (
    BlueprintGraphError,
    RuntimeFileBinding,
    descriptor_safe_open_supported,
    expanded_legacy_blueprint,
    load_validated_skill_blueprint_graph,
    open_runtime_file,
    open_runtime_python_package,
)

from .platforms import current_platform_name


class InvocationError(Exception):
    """Raised when a dispatcher request is invalid."""


@dataclass(frozen=True)
class _LoadedBlueprint:
    declaration: dict[str, Any]
    typed: bool
    portable_legacy: bool = False


@dataclass(frozen=True)
class ResolvedInvocationMetadata:
    """Descriptor-free result for policy inspection, dry-run, and tracing."""

    caller_skill: str
    target_skill: str
    script_interface: str
    target: str
    pattern: str
    cwd: Path
    command: list[str]
    stdin: bool

    def as_payload(self) -> dict[str, Any]:
        return {
            "caller_skill": self.caller_skill,
            "target_skill": self.target_skill,
            "script_interface": self.script_interface,
            "target": self.target,
            "pattern": self.pattern,
            "cwd": str(self.cwd),
            "command": list(self.command),
            "stdin": self.stdin,
        }


@dataclass(frozen=True)
class ResolvedInvocation:
    """Concrete invocation selected from a skill blueprint."""

    caller_skill: str
    target_skill: str
    script_interface: str
    target: str
    pattern: str
    cwd: Path
    command: list[str]
    stdin: bool
    env: dict[str, str] | None = None
    runtime_bindings: tuple[RuntimeFileBinding, ...] = ()

    @property
    def pass_fds(self) -> tuple[int, ...]:
        return tuple(binding.fd for binding in self.runtime_bindings if binding.fd >= 0)

    def close(self) -> None:
        for binding in self.runtime_bindings:
            binding.close()

    def __enter__(self) -> "ResolvedInvocation":
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        self.close()

    def _logical_command(self) -> list[str]:
        command = list(self.command)
        if self.runtime_bindings:
            binding = self.runtime_bindings[0]
            if len(command) >= 4 and command[3] in {"--source-fd", "--package-file"}:
                index = 3
                while index < len(command):
                    if command[index] == "--source-fd" and index + 1 < len(command):
                        del command[index : index + 2]
                    elif command[index] == "--package-file" and index + 2 < len(command):
                        del command[index : index + 3]
                    else:
                        break
            elif command and command[0].startswith("/proc/self/fd/"):
                command[0] = str(binding.path)
        return command

    def metadata(self) -> ResolvedInvocationMetadata:
        return ResolvedInvocationMetadata(
            caller_skill=self.caller_skill,
            target_skill=self.target_skill,
            script_interface=self.script_interface,
            target=self.target,
            pattern=self.pattern,
            cwd=self.cwd,
            command=self._logical_command(),
            stdin=self.stdin,
        )

    def as_payload(self) -> dict[str, Any]:
        return self.metadata().as_payload()


def get_repo_root(repo_root: Path | None = None) -> Path:
    """Resolve the AI repo root, preferring the installer-managed AI env var."""
    if repo_root is not None:
        return repo_root.resolve()

    env_root = os.environ.get("AI")
    if env_root:
        candidate = Path(env_root).expanduser().resolve()
        if (candidate / "skills").is_dir() and (candidate / "src").is_dir():
            return candidate

    return Path(__file__).resolve().parents[3]


def skills_root(repo_root: Path | None = None) -> Path:
    return get_repo_root(repo_root) / "skills"


def _portable_legacy_blueprint_snapshot(
    skill_name: str,
    path: Path,
    schema_path: Path,
) -> _LoadedBlueprint:
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        raise InvocationError(
            f"skill `{skill_name}` does not define blueprint.yaml"
        ) from exc
    except OSError as exc:
        raise InvocationError(f"{path}: cannot read blueprint: {exc}") from exc
    try:
        declaration = yaml.safe_load(payload.decode("utf-8")) or {}
    except (UnicodeError, yaml.YAMLError) as exc:
        raise InvocationError(f"{path}: cannot load blueprint YAML: {exc}") from exc
    if not isinstance(declaration, dict):
        raise InvocationError(f"{path}: top level must be a mapping")
    if declaration.get("schema_version") == 2 or "blueprint_type" in declaration:
        raise InvocationError(
            f"{path}: descriptor-safe no-follow file access is unavailable on this host"
        )
    try:
        schema = json.loads(schema_path.read_bytes().decode("utf-8"))
        if not isinstance(schema, dict):
            raise TypeError("schema top level must be a mapping")
        jsonschema.Draft7Validator.check_schema(schema)
        validator = jsonschema.Draft7Validator(schema)
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, jsonschema.SchemaError) as exc:
        raise InvocationError(
            f"{schema_path}: cannot load legacy blueprint schema: {exc}"
        ) from exc
    errors = sorted(
        validator.iter_errors(declaration),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        error_path = "$"
        for part in error.absolute_path:
            error_path += f"[{part}]" if isinstance(part, int) else f".{part}"
        raise InvocationError(
            f"{path}: legacy blueprint schema validation failed at "
            f"{error_path}: {error.message}"
        )
    return _LoadedBlueprint(declaration, False, True)


def _load_dispatch_blueprint(
    skill_name: str,
    repo_root: Path | None = None,
) -> _LoadedBlueprint:
    root = get_repo_root(repo_root)
    path = root / "skills" / skill_name / "blueprint.yaml"
    if not descriptor_safe_open_supported():
        if _current_platform_name() != "windows":
            raise InvocationError(
                f"{path}: descriptor-safe no-follow file access is unavailable on this host"
            )
        return _portable_legacy_blueprint_snapshot(
            skill_name,
            path,
            root / "references" / "blueprint" / "legacy-skill.schema.json",
        )
    try:
        graph = load_validated_skill_blueprint_graph(
            path.parent,
            root / "references" / "blueprint",
        )
        typed = (
            graph.root.declaration.get("schema_version") == 2
            or "blueprint_type" in graph.root.declaration
        )
        return _LoadedBlueprint(expanded_legacy_blueprint(graph), typed)
    except (BlueprintGraphError, OSError) as exc:
        raise InvocationError(str(exc)) from exc


def load_blueprint(skill_name: str, repo_root: Path | None = None) -> dict[str, Any]:
    return _load_dispatch_blueprint(skill_name, repo_root).declaration


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


def parse_canonical_target(target: str) -> tuple[str, str, str] | None:
    parts = target.split(".")
    if len(parts) != 3:
        return None
    skill_name, kind, interface_name = parts
    if not skill_name or not kind or not interface_name:
        return None
    return skill_name, kind, interface_name


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
        raise InvocationError("machine interface must have at least one pattern when `patterns` is declared")

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


def resolve_machine_interface_surface(
    target_blueprint: dict[str, Any],
    interface_name: str,
) -> tuple[dict[str, Any], str]:
    interfaces = expect_mapping(target_blueprint.get("interfaces"), "interfaces")
    machine = expect_mapping(interfaces.get("machine"), "interfaces.machine")
    spec = machine.get(interface_name)
    if not isinstance(spec, dict):
        raise InvocationError(f"skill does not define machine interface `{interface_name}`")
    return spec, interface_name


def _interface_version(interface_spec: dict[str, Any], context: str) -> int:
    version = interface_spec.get("version")
    if not isinstance(version, int) or version < 1:
        raise InvocationError(f"{context}: interface `version` must be a positive integer")
    return version


def _declared_interface_uses(
    caller_blueprint: dict[str, Any],
    caller_skill: str,
    canonical_target: str,
    target_version: int,
) -> bool:
    interfaces = expect_mapping(caller_blueprint.get("interfaces"), f"{caller_skill}.interfaces")
    for namespace in ("machine", "llm"):
        specifications = expect_mapping(
            interfaces.get(namespace),
            f"{caller_skill}.interfaces.{namespace}",
        )
        for interface_name, interface_spec in specifications.items():
            if not isinstance(interface_spec, dict):
                continue
            context = f"{caller_skill}.{namespace}.{interface_name}.uses_interfaces"
            uses = expect_list(interface_spec.get("uses_interfaces"), context)
            for entry in uses:
                if not isinstance(entry, dict):
                    raise InvocationError(
                        f"{context}: entries must declare `interface` and `version`"
                    )
                if (
                    entry.get("interface") == canonical_target
                    and entry.get("version") == target_version
                ):
                    return True
    return False


def resolve_machine_interface(
    target_skill: str,
    target_blueprint: dict[str, Any],
    caller_skill: str,
    interface_name: str,
    script_args: list[str],
    stdin_requested: bool,
    repo_root: Path | None = None,
    *,
    require_platform_support: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], str]:
    interface_spec, resolved_name = resolve_machine_interface_surface(target_blueprint, interface_name)
    canonical_target = f"{target_skill}.machine.{resolved_name}"
    platform_name = _current_platform_name()
    platform_support = interface_spec.get("platform_support")
    if require_platform_support:
        platform_admitted = (
            isinstance(platform_support, dict)
            and platform_support.get(platform_name) is True
        )
    else:
        platform_admitted = not (
            isinstance(platform_support, dict)
            and platform_support.get(platform_name) is False
        )
    if not platform_admitted:
        raise InvocationError(
            f"interface `{canonical_target}` does not support platform `{platform_name}`"
        )
    invocation = expect_mapping(interface_spec.get("invocation"), "invocation")
    if invocation.get("kind") == "python_machine_interface" and script_args == ["--route-smoke"] and not stdin_requested:
        pattern_spec, pattern_name = {}, "route-smoke"
    else:
        pattern_spec, pattern_name = find_matching_pattern(interface_spec, script_args, stdin_requested)

    allow_all_skills = interface_spec.get("allow_all_skills", False)
    allowed_callers = expect_string_list(interface_spec.get("allowed_callers"), "allowed_callers")
    target_version = _interface_version(interface_spec, canonical_target)

    if caller_skill == target_skill:
        return interface_spec, pattern_spec, pattern_name

    if not allow_all_skills and not allowed_callers:
        raise InvocationError(f"interface `{canonical_target}` is internal-only")

    if not allow_all_skills and caller_skill not in allowed_callers:
        raise InvocationError(
            f"skill `{caller_skill}` is not in allowed_callers for `{canonical_target}`"
        )

    caller_blueprint = load_blueprint(caller_skill, repo_root=repo_root)
    if not _declared_interface_uses(
        caller_blueprint,
        caller_skill,
        canonical_target,
        target_version,
    ):
        raise InvocationError(
            f"caller skill `{caller_skill}` does not declare uses_interfaces entry "
            f"for `{canonical_target}` version {target_version}"
        )

    return interface_spec, pattern_spec, pattern_name


def build_machine_runtime(
    target_skill: str,
    interface_name: str,
    interface_spec: dict[str, Any],
    script_args: list[str],
    repo_root: Path | None = None,
    *,
    legacy_compatibility: bool = False,
) -> tuple[
    Path,
    list[str],
    dict[str, str] | None,
    tuple[RuntimeFileBinding, ...],
]:
    invocation = expect_mapping(interface_spec.get("invocation"), "invocation")
    kind = invocation.get("kind")
    root = get_repo_root(repo_root)
    skill_root = root / "skills" / target_skill
    if kind == "python_module":
        module = invocation.get("module")
        if not isinstance(module, str) or not module.strip():
            raise InvocationError(f"{target_skill}.machine.{interface_name}: invocation needs non-empty `module`")
        env = os.environ.copy()
        src_root = root / "src"
        entries = [str(skill_root), str(src_root)]
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join(entries + ([current] if current else []))
        env["PYTHONIOENCODING"] = "utf-8:strict"
        return root, [sys.executable, "-m", module, *script_args], env, ()
    if kind == "python_machine_interface":
        entrypoint = invocation.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.strip():
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: python_machine_interface invocation "
                "needs non-empty `entrypoint`"
            )
        args_prefix = invocation.get("args_prefix", [])
        if not isinstance(args_prefix, list) or not all(isinstance(token, str) and token for token in args_prefix):
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: python_machine_interface invocation "
                "needs string list `args_prefix`"
            )
        env = os.environ.copy()
        src_root = root / "src"
        entries = [str(skill_root), str(src_root)]
        current = env.get("PYTHONPATH")
        env["PYTHONPATH"] = os.pathsep.join(entries + ([current] if current else []))
        env["PYTHONIOENCODING"] = "utf-8:strict"
        if legacy_compatibility and not descriptor_safe_open_supported():
            return (
                skill_root,
                [
                    sys.executable,
                    "-m",
                    "officina.runtime.python_machine_interface_runner",
                    entrypoint,
                    *args_prefix,
                    *script_args,
                ],
                env,
                (),
            )
        module_text, separator, class_name = entrypoint.partition(":")
        module_path = Path(module_text)
        if (
            separator != ":"
            or not module_text
            or not class_name
            or module_path.is_absolute()
            or ".." in module_path.parts
            or not module_path.parts
            or module_path.parts[0] != "_rtx"
        ):
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: entrypoint must be "
                "`_rtx/path.py:ClassName` without parent traversal"
            )
        try:
            package_bindings = open_runtime_python_package(
                skill_root / "_rtx",
                skill_root,
                root,
            )
        except BlueprintGraphError as exc:
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: {exc}"
            ) from exc
        source_path = Path(os.path.abspath(skill_root / module_path))
        source_binding = next(
            (binding for binding in package_bindings if binding.path == source_path),
            None,
        )
        if source_binding is None:
            for binding in package_bindings:
                binding.close()
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: entrypoint is not a "
                f"regular Python package source: {module_text}"
            )
        package_arguments = [
            token
            for binding in package_bindings
            for token in (
                "--package-file",
                str(binding.fd),
                binding.path.relative_to(skill_root).as_posix(),
            )
        ]
        return (
            skill_root,
            [
                sys.executable,
                "-m",
                "officina.runtime.python_machine_interface_runner",
                "--source-fd",
                str(source_binding.fd),
                *package_arguments,
                entrypoint,
                *args_prefix,
                *script_args,
            ],
            env,
            package_bindings,
        )
    if kind == "command":
        argv = invocation.get("argv")
        if not isinstance(argv, list) or not all(isinstance(token, str) and token for token in argv):
            raise InvocationError(f"{target_skill}.machine.{interface_name}: invocation needs non-empty `argv`")
        return skill_root, [*argv, *script_args], None, ()
    if kind == "command_file":
        path = invocation.get("path")
        args_prefix = invocation.get("args_prefix", [])
        if not isinstance(path, str) or not path.startswith("_cx/"):
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: command_file path must be under `_cx/`"
            )
        relative_path = Path(path)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: command_file path cannot use parent traversal"
            )
        if not isinstance(args_prefix, list) or not all(
            isinstance(token, str) and token for token in args_prefix
        ):
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: command_file invocation "
                "needs string list `args_prefix`"
            )
        try:
            command_binding = open_runtime_file(
                skill_root / path,
                skill_root,
                root,
                executable=True,
            )
        except BlueprintGraphError as exc:
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: {exc}"
            ) from exc
        try:
            command_binding.path.relative_to(Path(os.path.abspath(skill_root / "_cx")))
        except ValueError as exc:
            command_binding.close()
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: command file must resolve under `_cx/`"
            ) from exc
        try:
            executable_path = command_binding.proc_path()
        except BlueprintGraphError as exc:
            command_binding.close()
            raise InvocationError(
                f"{target_skill}.machine.{interface_name}: {exc}"
            ) from exc
        return (
            skill_root,
            [executable_path, *args_prefix, *script_args],
            None,
            (command_binding,),
        )
    raise InvocationError(f"{target_skill}.machine.{interface_name}: unsupported invocation kind `{kind}`")


def _current_platform_name() -> str:
    return current_platform_name()


def resolve_dispatch(
    *,
    caller_skill: str,
    target: str | None = None,
    target_skill: str | None = None,
    script_interface: str | None = None,
    args: list[str] | None = None,
    stdin_requested: bool = False,
    repo_root: Path | None = None,
) -> ResolvedInvocation:
    args = args or []
    if not caller_skill.strip():
        raise InvocationError("caller_skill must be a non-empty string")
    caller_skill = caller_skill.strip()

    parsed_target = parse_canonical_target(target) if isinstance(target, str) else None
    if parsed_target is not None:
        target_skill_name, kind, interface_name = parsed_target
        if kind != "machine":
            raise InvocationError("dispatcher only executes `.machine.` targets")
        target_snapshot = _load_dispatch_blueprint(
            target_skill_name,
            repo_root=repo_root,
        )
        target_blueprint = target_snapshot.declaration
        interface_spec, _pattern_spec, pattern_name = resolve_machine_interface(
            target_skill_name,
            target_blueprint,
            caller_skill,
            interface_name,
            args,
            stdin_requested,
            repo_root=repo_root,
            require_platform_support=target_snapshot.portable_legacy,
        )
        cwd, command, env, runtime_bindings = build_machine_runtime(
            target_skill_name,
            interface_name,
            interface_spec,
            args,
            repo_root=repo_root,
            legacy_compatibility=not target_snapshot.typed,
        )
        return ResolvedInvocation(
            caller_skill=caller_skill,
            target_skill=target_skill_name,
            script_interface=interface_name,
            target=target,
            pattern=pattern_name,
            cwd=cwd,
            command=command,
            stdin=stdin_requested,
            env=env,
            runtime_bindings=runtime_bindings,
        )

    if target_skill is None or script_interface is None:
        raise InvocationError("dispatch requires a canonical target or target_skill and machine interface")

    target_snapshot = _load_dispatch_blueprint(target_skill, repo_root=repo_root)
    target_blueprint = target_snapshot.declaration
    interface_spec, _pattern_spec, pattern_name = resolve_machine_interface(
        target_skill,
        target_blueprint,
        caller_skill,
        script_interface,
        args,
        stdin_requested,
        repo_root=repo_root,
        require_platform_support=target_snapshot.portable_legacy,
    )
    cwd, command, env, runtime_bindings = build_machine_runtime(
        target_skill,
        script_interface,
        interface_spec,
        args,
        repo_root=repo_root,
        legacy_compatibility=not target_snapshot.typed,
    )
    return ResolvedInvocation(
        caller_skill=caller_skill,
        target_skill=target_skill,
        script_interface=script_interface,
        target=f"{target_skill}.machine.{script_interface}",
        pattern=pattern_name,
        cwd=cwd,
        command=command,
        stdin=stdin_requested,
        env=env,
        runtime_bindings=runtime_bindings,
    )


def resolve_dispatch_metadata(**kwargs: Any) -> ResolvedInvocationMetadata:
    """Resolve policy and return metadata after deterministically closing bindings."""

    with resolve_dispatch(**kwargs) as resolved:
        return resolved.metadata()


def dispatch(
    *,
    caller_skill: str,
    target: str | None = None,
    target_skill: str | None = None,
    script_interface: str | None = None,
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
        target=target,
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
    if resolved.env is not None:
        run_kwargs["env"] = resolved.env
    if resolved.pass_fds:
        run_kwargs["pass_fds"] = resolved.pass_fds
    if timeout is not None:
        run_kwargs["timeout"] = timeout
    if stdin is not None:
        run_kwargs["input"] = stdin
    if text is not None:
        run_kwargs["text"] = text
    elif isinstance(stdin, str):
        run_kwargs["text"] = True
    if run_kwargs.get("text"):
        run_kwargs["encoding"] = "utf-8"
        run_kwargs["errors"] = "strict"

    try:
        try:
            return subprocess.run(resolved.command, **run_kwargs)
        except OSError as exc:
            raise InvocationError(
                f"{resolved.target}: launch failed: {exc}"
            ) from exc
    finally:
        resolved.close()
