#!/usr/bin/env python3
"""Audit-record certifier for blueprint-backed installed skills."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Sequence

import yaml

from officina.runtime.python_machine_interface import (
    DispatchCall,
    PythonArgvMachineInterface,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_RECORD_NAME = ".last_audit.json"
SCHEMA_VERSION = 1
WRITER = "skill-audit@1"
TEXT_FILE_SUFFIXES = {".md", ".markdown", ".py", ".txt", ".yaml", ".yml", ".json"}
IMPLICIT_DIRECTORY_PATTERNS = (
    re.compile(r"\b(?:look|scan|search|inspect|read)\s+under\s+([A-Za-z0-9_./\\-]+)", re.IGNORECASE),
    re.compile(r"\b(?:executables|scripts|tools|helpers|modules)\s+under\s+([A-Za-z0-9_./\\-]+)", re.IGNORECASE),
    re.compile(r"\b([A-Za-z0-9_./\\-]+)\s+(?:directory|folder)\s+for\s+(?:executables|scripts|tools|helpers|modules)", re.IGNORECASE),
)
GENERATED_BLOCK_RE = re.compile(
    r"<!-- BEGIN BLUEPRINT (?:CONTRACT|INTERFACES) -->.*?<!-- END BLUEPRINT (?:CONTRACT|INTERFACES) -->",
    re.DOTALL,
)
COMMAND_LINE_RE = re.compile(
    r"^\s*(?:[-*]\s*)?(?:`{0,3})\s*"
    r"(?:python3?|bash|sh|pytest|npm|node|dispatcher|\./|/[^/\s]+/|_rtx/|scripts/)\b"
)
EXECUTION_VERB_RE = re.compile(
    r"\b(?:run|execute|invoke|launch|shell out to|call)\b.*"
    r"(?:`[^`]*(?:python3?|bash|sh|pytest|npm|node|dispatcher|_rtx/|scripts/|\./)[^`]*`|"
    r"\b(?:python3?|bash|sh|pytest|npm|node|dispatcher)\b|_rtx/|scripts/|\./)",
    re.IGNORECASE,
)
IMPLEMENTATION_PATH_RE = re.compile(r"(?:^|[`'\"\s(])(?:_rtx/|scripts/)[A-Za-z0-9_./\\-]+")


class AuditError(RuntimeError):
    """Raised when certification cannot safely continue."""


class Dispatcher(Protocol):
    """Small protocol for dispatcher-backed calls, with test doubles."""

    def dispatch(
        self,
        key: str,
        *,
        args: Sequence[str] | None = None,
        stdin: str | bytes | None = None,
        timeout: float | None = None,
        capture_output: bool = True,
        check: bool = False,
        text: bool | None = None,
        repo_root: Path | None = None,
    ) -> Any:
        ...


@dataclass(frozen=True)
class CommandResult:
    name: str
    command: list[str]
    exit_code: int
    stdout: str
    stderr: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0

    def as_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "command": self.command,
            "exit_code": self.exit_code,
            "passed": self.passed,
            "stdout_tail": tail(self.stdout),
            "stderr_tail": tail(self.stderr),
        }


@dataclass(frozen=True)
class Finding:
    kind: str
    message: str
    path: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload = {"kind": self.kind, "message": self.message}
        if self.path is not None:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True)
class TargetHash:
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    skill_root: Path
    hashes: dict[str, Any]

    @classmethod
    def from_payload(cls, item: dict[str, Any]) -> "TargetHash":
        skill = expect_string(item.get("skill"), "skill")
        package_root = Path(expect_string(item.get("package_root"), "package_root"))
        skills_root = Path(expect_string(item.get("skills_root"), "skills_root"))
        hashes = item.get("hashes")
        if not isinstance(hashes, dict):
            raise AuditError(f"{skill}: hash payload is missing hashes object")
        return cls(
            skill=skill,
            source=expect_string(item.get("source"), "source"),
            package_root=package_root,
            skills_root=skills_root,
            skill_root=skills_root / skill,
            hashes=hashes,
        )


@dataclass(frozen=True)
class AuditOutcome:
    skill: str
    source: str
    skill_root: Path
    record_path: Path
    status: str
    semantic_findings: list[Finding]

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "skill_root": self.skill_root.as_posix(),
            "record_path": self.record_path.as_posix(),
            "status": self.status,
            "semantic_findings": [finding.as_payload() for finding in self.semantic_findings],
        }


def tail(text: str, max_chars: int = 4000) -> str:
    if len(text) <= max_chars:
        return text
    return text[-max_chars:]


def expect_string(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuditError(f"hash payload field `{field}` must be a non-empty string")
    return value


def run_local_command(name: str, command: list[str], *, repo_root: Path = REPO_ROOT) -> CommandResult:
    completed = subprocess.run(
        command,
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    return CommandResult(
        name=name,
        command=command,
        exit_code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def run_mechanical_checks(dispatcher: Dispatcher, *, repo_root: Path = REPO_ROOT) -> list[CommandResult]:
    """Run the global mechanical gate before any audit record is written."""

    sync = dispatcher.dispatch("sync-blueprints", args=["--check"], text=True, check=False)
    results = [
        CommandResult(
            name="blueprint-sync",
            command=["skill-maker.machine.sync-blueprints", "--check"],
            exit_code=sync.returncode,
            stdout=sync.stdout or "",
            stderr=sync.stderr or "",
        ),
        run_local_command("validators", [sys.executable, "validators/runner.py"], repo_root=repo_root),
        run_local_command("tests", [sys.executable, "scripts/run-python-tests.py", "--suite", "precommit"], repo_root=repo_root),
    ]
    failed = [result for result in results if not result.passed]
    if failed:
        first = failed[0]
        raise AuditError(f"mechanical check failed: {first.name}")
    return results


def compute_hash_payload(dispatcher: Dispatcher, target: str | None = None) -> dict[str, Any]:
    args = ["compute-hashes", "--json"]
    if target:
        path = Path(target).expanduser()
        if is_path_like(target) and path.exists():
            args = ["compute-hashes", "--skill-root", str(path.resolve()), "--json"]
        else:
            args = ["compute-hashes", target, "--json"]
    completed = dispatcher.dispatch("compute-hashes", args=args, text=True, check=False)
    if completed.returncode != 0:
        raise AuditError((completed.stderr or completed.stdout or "compute-hashes failed").strip())
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuditError(f"compute-hashes did not return JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AuditError("compute-hashes returned non-object JSON")
    return payload


def collect_targets(dispatcher: Dispatcher, targets: Sequence[str]) -> list[TargetHash]:
    """Resolve requested names/paths through the drift hash interface."""

    raw_items: list[dict[str, Any]] = []
    if targets:
        for target in targets:
            raw_items.extend(hash_items(compute_hash_payload(dispatcher, target)))
    else:
        raw_items.extend(hash_items(compute_hash_payload(dispatcher)))

    resolved = [TargetHash.from_payload(item) for item in raw_items]
    seen: set[tuple[str, str]] = set()
    deduped: list[TargetHash] = []
    for target in resolved:
        key = (target.skill, target.skill_root.resolve(strict=False).as_posix())
        if key in seen:
            continue
        seen.add(key)
        deduped.append(target)
    if not deduped:
        raise AuditError("no blueprint-backed target skills were resolved")
    return deduped


def hash_items(payload: dict[str, Any]) -> list[dict[str, Any]]:
    items = payload.get("skills")
    if not isinstance(items, list):
        raise AuditError("compute-hashes payload is missing skills list")
    if not all(isinstance(item, dict) for item in items):
        raise AuditError("compute-hashes skills entries must be objects")
    return items


def is_path_like(value: str) -> bool:
    return "/" in value or "\\" in value or value.startswith((".", "~"))


def load_blueprint(skill_root: Path) -> dict[str, Any]:
    path = skill_root / "blueprint.yaml"
    if not path.is_file():
        raise AuditError(f"{skill_root.as_posix()}: missing blueprint.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise AuditError(f"{path.as_posix()}: top level must be a mapping")
    return raw


def semantic_findings(target: TargetHash) -> list[Finding]:
    """Return deterministic exactness findings the runtime can check."""

    blueprint = load_blueprint(target.skill_root)
    findings: list[Finding] = []
    findings.extend(check_declared_roots_exist(target.skill_root, target.package_root, blueprint))
    findings.extend(check_runtime_entrypoints_exist(target.skill_root, blueprint))
    findings.extend(check_skill_md_execution_logic(target.skill_root))
    findings.extend(check_implicit_directory_references(target.skill_root, target.package_root, blueprint))
    return findings


def check_declared_roots_exist(skill_root: Path, package_root: Path, blueprint: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for interface_name, spec in iter_interfaces(blueprint):
        for field in ("directly_reads", "directly_executes", "directly_writes"):
            value = spec.get(field, [])
            if not isinstance(value, list):
                findings.append(Finding("invalid-blueprint", f"{interface_name}.{field} must be a list"))
                continue
            for root in value:
                if not isinstance(root, str):
                    findings.append(Finding("invalid-blueprint", f"{interface_name}.{field} contains a non-string root"))
                    continue
                path = resolve_declared_root(skill_root, package_root, root)
                if path is not None and not (path.exists() or path.is_symlink()):
                    findings.append(
                        Finding(
                            "missing-declared-root",
                            f"{interface_name}.{field} declares missing root `{root}`",
                            path.as_posix(),
                        )
                    )
    return findings


def check_runtime_entrypoints_exist(skill_root: Path, blueprint: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for interface_name, spec in iter_interfaces(blueprint, namespaces=("machine",)):
        runtime = spec.get("runtime")
        if not isinstance(runtime, dict):
            findings.append(Finding("invalid-blueprint", f"{interface_name}.runtime must be a mapping"))
            continue
        kind = runtime.get("kind")
        if kind == "python_machine_interface":
            entrypoint = runtime.get("entrypoint")
            if not isinstance(entrypoint, str) or ":" not in entrypoint:
                findings.append(Finding("invalid-blueprint", f"{interface_name}.runtime.entrypoint is invalid"))
                continue
            path_text = entrypoint.split(":", 1)[0]
            path = skill_root / path_text
            if not path.is_file():
                findings.append(
                    Finding("missing-runtime-entrypoint", f"{interface_name} entrypoint does not exist", path.as_posix())
                )
        elif kind == "command":
            argv = runtime.get("argv")
            if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
                findings.append(Finding("invalid-blueprint", f"{interface_name}.runtime.argv is invalid"))
                continue
            first = argv[0]
            if "/" in first or "\\" in first:
                path = skill_root / first
                if not path.exists():
                    findings.append(
                        Finding("missing-runtime-entrypoint", f"{interface_name} command does not exist", path.as_posix())
                    )
    return findings


def check_implicit_directory_references(
    skill_root: Path,
    package_root: Path,
    blueprint: dict[str, Any],
) -> list[Finding]:
    declared_roots = collect_declared_paths(skill_root, package_root, blueprint)
    findings: list[Finding] = []
    for path in iter_text_files(skill_root):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in IMPLICIT_DIRECTORY_PATTERNS:
            for match in pattern.finditer(text):
                token = match.group(1).strip(".,;:)]}\"'")
                if not token or token.startswith(("http://", "https://")):
                    continue
                candidate = (path.parent / token).resolve(strict=False)
                try:
                    candidate.relative_to(skill_root.resolve())
                except ValueError:
                    continue
                if candidate.is_dir() and not any(covers(root, candidate) for root in declared_roots):
                    findings.append(
                        Finding(
                            "implicit-root-not-declared",
                            f"{relative_to(path, skill_root)} implicitly references directory `{token}`",
                            candidate.as_posix(),
                        )
                    )
    return dedupe_findings(findings)


def check_skill_md_execution_logic(skill_root: Path) -> list[Finding]:
    """Flag hand-authored SKILL.md execution instructions not routed through interfaces."""

    path = skill_root / "SKILL.md"
    if not path.is_file():
        return [Finding("missing-skill-file", "SKILL.md is missing", path.as_posix())]

    text = strip_generated_blocks(path.read_text(encoding="utf-8"))
    findings: list[Finding] = []
    in_fence = False
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            continue
        if not stripped:
            continue
        reason = unencapsulated_execution_reason(stripped, in_fence=in_fence)
        if reason is not None:
            findings.append(
                Finding(
                    "unencapsulated-execution",
                    f"SKILL.md line {line_number} contains execution logic outside an interface: {reason}",
                    path.as_posix(),
                )
            )
    return findings


def strip_generated_blocks(text: str) -> str:
    return GENERATED_BLOCK_RE.sub("", text)


def unencapsulated_execution_reason(line: str, *, in_fence: bool = False) -> str | None:
    normalized = line.strip()
    if not normalized:
        return None
    if COMMAND_LINE_RE.search(normalized):
        return "command-like instruction"
    if EXECUTION_VERB_RE.search(normalized):
        return "execution verb with command or implementation path"
    if IMPLEMENTATION_PATH_RE.search(normalized):
        return "direct implementation path reference"
    if in_fence and re.search(r"\b(?:python3?|bash|sh|pytest|npm|node|dispatcher)\b", normalized):
        return "command reference inside code block"
    return None


def iter_interfaces(
    blueprint: dict[str, Any],
    *,
    namespaces: Sequence[str] = ("llm", "machine"),
) -> list[tuple[str, dict[str, Any]]]:
    interfaces = blueprint.get("interfaces")
    if not isinstance(interfaces, dict):
        return []
    result: list[tuple[str, dict[str, Any]]] = []
    for namespace in namespaces:
        entries = interfaces.get(namespace)
        if not isinstance(entries, dict):
            continue
        for name, spec in sorted(entries.items()):
            if isinstance(spec, dict):
                result.append((f"{namespace}.{name}", spec))
    return result


def resolve_declared_root(skill_root: Path, package_root: Path, root: str) -> Path | None:
    if os.path.isabs(root) or ".." in Path(root).parts:
        return None
    if root.startswith("$repo/"):
        return (package_root / root[len("$repo/") :]).resolve(strict=False)
    return (skill_root / root).resolve(strict=False)


def collect_declared_paths(skill_root: Path, package_root: Path, blueprint: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for _interface_name, spec in iter_interfaces(blueprint):
        binding = spec.get("binding")
        if isinstance(binding, dict):
            binding_path = binding.get("path")
            if isinstance(binding_path, str):
                path = resolve_declared_root(skill_root, package_root, binding_path)
                if path is not None:
                    paths.append(path)
        for field in ("directly_reads", "directly_executes", "directly_writes"):
            value = spec.get(field, [])
            if isinstance(value, list):
                for root in value:
                    if isinstance(root, str):
                        path = resolve_declared_root(skill_root, package_root, root)
                        if path is not None:
                            paths.append(path)
    return paths


def iter_text_files(skill_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(skill_root.rglob("*"), key=lambda item: item.as_posix()):
        if AUDIT_RECORD_NAME in path.parts or "__pycache__" in path.parts:
            continue
        if path.is_file() and path.suffix.lower() in TEXT_FILE_SUFFIXES:
            files.append(path)
    return files


def covers(root: Path, candidate: Path) -> bool:
    root = root.resolve(strict=False)
    candidate = candidate.resolve(strict=False)
    if root == candidate:
        return True
    if root.is_dir() or not root.suffix:
        try:
            candidate.relative_to(root)
            return True
        except ValueError:
            return False
    return False


def dedupe_findings(findings: Sequence[Finding]) -> list[Finding]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[Finding] = []
    for finding in findings:
        key = (finding.kind, finding.message, finding.path)
        if key in seen:
            continue
        seen.add(key)
        result.append(finding)
    return result


def relative_to(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def git_commit(repo_root: Path = REPO_ROOT) -> str | None:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
    )
    if completed.returncode != 0:
        return None
    return completed.stdout.strip() or None


def build_record(
    target: TargetHash,
    *,
    mechanical_checks: Sequence[CommandResult],
    semantic_results: Sequence[Finding],
    recorded_at: str | None = None,
    repo_root: Path = REPO_ROOT,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "skill": target.skill,
        "recorded_at": recorded_at or datetime.now().astimezone().isoformat(timespec="seconds"),
        "writer": WRITER,
        "git_commit": git_commit(repo_root),
        "checks": {
            "mechanical": [result.as_payload() for result in mechanical_checks],
            "semantic": {
                "passed": not semantic_results,
                "findings": [finding.as_payload() for finding in semantic_results],
            },
        },
        "hashes": target.hashes,
    }


def write_record(path: Path, record: dict[str, Any]) -> bytes | None:
    previous = path.read_bytes() if path.exists() else None
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return previous


def restore_record(path: Path, previous: bytes | None) -> None:
    if previous is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    else:
        path.write_bytes(previous)


def verify_post_write(dispatcher: Dispatcher, target: TargetHash) -> None:
    completed = dispatcher.dispatch(
        "drift-status",
        args=["status", "--skill-root", str(target.skill_root.resolve()), "--json"],
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AuditError((completed.stderr or completed.stdout or "drift-status failed").strip())
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise AuditError(f"drift-status did not return JSON: {exc}") from exc
    skills = payload.get("skills")
    if not isinstance(skills, list) or not skills:
        raise AuditError("drift-status returned no skill reports")
    stale = [item for item in skills if isinstance(item, dict) and item.get("derived_status") != "audit-current"]
    if stale:
        raise AuditError(f"post-write drift verification failed for {target.skill}")


def certify(
    dispatcher: Dispatcher,
    *,
    targets: Sequence[str],
    repo_root: Path = REPO_ROOT,
    skip_mechanical: bool = False,
    recorded_at: str | None = None,
) -> tuple[list[CommandResult], list[AuditOutcome]]:
    mechanical = [] if skip_mechanical else run_mechanical_checks(dispatcher, repo_root=repo_root)
    resolved_targets = collect_targets(dispatcher, targets)
    outcomes: list[AuditOutcome] = []

    for target in resolved_targets:
        findings = semantic_findings(target)
        if findings:
            raise AuditError(f"semantic exactness check failed for {target.skill}")
        record_path = target.skill_root / AUDIT_RECORD_NAME
        previous = write_record(
            record_path,
            build_record(target, mechanical_checks=mechanical, semantic_results=findings, recorded_at=recorded_at),
        )
        try:
            verify_post_write(dispatcher, target)
        except Exception:
            restore_record(record_path, previous)
            raise
        outcomes.append(
            AuditOutcome(
                skill=target.skill,
                source=target.source,
                skill_root=target.skill_root,
                record_path=record_path,
                status="audit-current",
                semantic_findings=findings,
            )
        )
    return mechanical, outcomes


def render_text(outcomes: Sequence[AuditOutcome]) -> str:
    lines = [
        "# Skill Audit Report",
        "",
        "| Source | Skill | Status | Record |",
        "|---|---|---|---|",
    ]
    for outcome in outcomes:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(outcome.source),
                    markdown_cell(outcome.skill),
                    markdown_cell(outcome.status),
                    markdown_cell(outcome.record_path.as_posix()),
                ]
            )
            + " |"
        )
    return "\n".join(lines) + "\n"


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Certify skill audit records.")
    parser.add_argument("command", choices=["certify"])
    parser.add_argument("targets", nargs="*")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument("--skip-mechanical", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--recorded-at", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None, dispatcher: Dispatcher | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    runtime = dispatcher or Interface()
    try:
        mechanical, outcomes = certify(
            runtime,
            targets=args.targets,
            skip_mechanical=args.skip_mechanical,
            recorded_at=args.recorded_at,
        )
    except AuditError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    payload = {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "mechanical": [result.as_payload() for result in mechanical],
        "certified": [outcome.as_payload() for outcome in outcomes],
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(outcomes), end="")
    return 0


class Interface(PythonArgvMachineInterface):
    """Dispatcher adapter for skill audit certification."""

    dispatches = {
        "compute-hashes": DispatchCall(
            caller_skill="skill-audit",
            target_skill="skill-drift",
            interface="compute-hashes",
            smoke_args=("compute-hashes", "--json"),
        ),
        "drift-status": DispatchCall(
            caller_skill="skill-audit",
            target_skill="skill-drift",
            interface="drift-status",
            smoke_args=("status", "--json"),
        ),
        "sync-blueprints": DispatchCall(
            caller_skill="skill-audit",
            target_skill="skill-maker",
            interface="sync-blueprints",
            smoke_args=("--check",),
        ),
    }

    def run(self, argv: list[str]) -> int:
        return main(argv, dispatcher=self)


if __name__ == "__main__":
    raise SystemExit(main())
