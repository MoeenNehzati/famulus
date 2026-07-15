#!/usr/bin/env python3
"""Audit-record certifier for blueprint-backed installed skills."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Protocol, Sequence

import yaml

from officina.common.artifact_health import (
    GraphHealthReport,
    NodeHealthStatus,
    blueprint_schema_hash,
    build_node_health_record,
    check_graph_health,
    compute_node_hash_states,
    health_edges,
    health_node_ids,
    health_path_for_node,
    health_postorder_node_ids,
    local_input_paths_for_node,
    node_requires_refresh,
)
from officina.common.audit_records import (
    attach_record_digest,
    load_or_create_hmac_key,
    record_digest_matches,
)
from officina.common.atomic_files import atomic_replace_bytes
from officina.common.blueprint_graph import (
    SkillBlueprintGraph,
    load_validated_skill_blueprint_graph,
)
from officina.common.git_provenance import (
    GitSnapshot,
    capture_git_snapshot,
    check_commit_readiness,
    snapshot_head_matches,
)
from officina.common.pooled_blueprint import (
    certify_pooled_review,
    check_pooled_review,
    pooled_review_health_path,
    pooled_review_path,
    render_pooled_review,
)
from officina.runtime.python_machine_interface import (
    DispatchCall,
    PythonArgvMachineInterface,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
AUDIT_RECORD_NAME = ".last_audit.json"
OUTPUT_SCHEMA_VERSION = 1
TEXT_FILE_SUFFIXES = {".md", ".markdown", ".py", ".txt", ".yaml", ".yml", ".json"}
REQUIRED_SCHEMA_INPUTS = (
    "behavior-source.schema.json",
    "common.schema.json",
    "default-llm-interface.schema.json",
    "health.schema.json",
    "legacy-skill.schema.json",
    "llm-interface.schema.json",
    "machine-interface.schema.json",
    "pooled-review.schema.json",
    "schema.annotated-draft.json",
    "schema.json",
    "schema-meta.json",
    "skill.schema.json",
    "template.yaml",
)
POOL_STATUSES = frozenset({"reused", "written", "not-written", "failed"})
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
class NodeAuditOutcome:
    node_id: str
    semantic_status: str
    health_status: str
    stamp_worthy: bool
    stamp_status: str
    reasons: tuple[str, ...]
    record_path: Path | None

    def as_payload(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "semantic_status": self.semantic_status,
            "health_status": self.health_status,
            "stamp_worthy": self.stamp_worthy,
            "stamp_status": self.stamp_status,
            "reasons": list(self.reasons),
            "record_path": self.record_path.as_posix() if self.record_path else None,
        }


@dataclass(frozen=True)
class AuditOutcome:
    skill: str
    source: str
    skill_root: Path
    semantic_status: str
    stamp_worthy: bool
    stamp_status: str
    nodes: tuple[NodeAuditOutcome, ...]
    pool_status: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "skill_root": self.skill_root.as_posix(),
            "semantic_status": self.semantic_status,
            "stamp_worthy": self.stamp_worthy,
            "stamp_status": self.stamp_status,
            "nodes": [node.as_payload() for node in self.nodes],
            "pool_status": self.pool_status,
        }


@dataclass(frozen=True)
class AuditContext:
    graph: SkillBlueprintGraph
    repo_root: Path
    schema_root: Path
    policy_hash: str
    schema_hash: str
    key: bytes
    snapshot: GitSnapshot | None
    node_checks: Mapping[str, tuple[dict[str, object], ...]]
    raw_evidence: tuple[CommandResult, ...]


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


def collect_exact_target(dispatcher: Dispatcher, request: str) -> TargetHash:
    """Resolve one explicit request without admitting provider substitutions."""

    items = hash_items(compute_hash_payload(dispatcher, request))
    if len(items) != 1:
        raise AuditError(
            f"explicit request `{request}` resolved to {len(items)} compute-hashes results; expected exactly one"
        )
    target = TargetHash.from_payload(items[0])
    if is_path_like(request):
        requested_root = Path(request).expanduser().resolve(strict=False)
        if target.skill_root.resolve(strict=False) != requested_root:
            raise AuditError(
                f"explicit path request `{request}` resolved to wrong skill root "
                f"`{target.skill_root.as_posix()}`"
            )
    elif target.skill != request:
        raise AuditError(
            f"explicit skill request `{request}` resolved to wrong skill `{target.skill}`"
        )
    return target


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
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise AuditError(f"{path.as_posix()}: invalid YAML: {exc}") from exc
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
        for root, source in behavior_source_paths(spec):
            path = resolve_declared_root(skill_root, package_root, root)
            if path is not None and not (path.exists() or path.is_symlink()):
                findings.append(
                    Finding(
                        "missing-declared-root",
                        f"{interface_name}.{source} declares missing root `{root}`",
                        path.as_posix(),
                    )
                )
    return findings


def check_runtime_entrypoints_exist(skill_root: Path, blueprint: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    for interface_name, spec in iter_interfaces(blueprint, namespaces=("machine",)):
        invocation = spec.get("invocation")
        if not isinstance(invocation, dict):
            findings.append(Finding("invalid-blueprint", f"{interface_name}.invocation must be a mapping"))
            continue
        kind = invocation.get("kind")
        if kind == "python_machine_interface":
            entrypoint = invocation.get("entrypoint")
            if not isinstance(entrypoint, str) or ":" not in entrypoint:
                findings.append(Finding("invalid-blueprint", f"{interface_name}.invocation.entrypoint is invalid"))
                continue
            path_text = entrypoint.split(":", 1)[0]
            path = skill_root / path_text
            if not path.is_file():
                findings.append(
                    Finding("missing-runtime-entrypoint", f"{interface_name} entrypoint does not exist", path.as_posix())
                )
        elif kind == "command":
            argv = invocation.get("argv")
            if not isinstance(argv, list) or not argv or not isinstance(argv[0], str):
                findings.append(Finding("invalid-blueprint", f"{interface_name}.invocation.argv is invalid"))
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
        for root, _source in behavior_source_paths(spec):
            path = resolve_declared_root(skill_root, package_root, root)
            if path is not None:
                paths.append(path)
    return paths


def behavior_source_paths(spec: dict[str, Any]) -> list[tuple[str, str]]:
    """Return declared behavior-shaping paths for an interface."""

    paths: list[tuple[str, str]] = []
    binding = spec.get("binding")
    if isinstance(binding, dict):
        binding_path = binding.get("path")
        if isinstance(binding_path, str):
            paths.append((binding_path, "binding"))
    for source_label, container in (("behavior_sources", spec), ("invocation.behavior_sources", spec.get("invocation"))):
        if not isinstance(container, dict):
            continue
        value = container.get("behavior_sources", [])
        if not isinstance(value, list):
            continue
        for entry in value:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                paths.append((entry["path"], source_label))
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


def build_record(
    target: TargetHash,
    *,
    mechanical_checks: Sequence[CommandResult],
    semantic_results: Sequence[Finding],
    source: Mapping[str, object],
    timestamp: str | None = None,
) -> dict[str, Any]:
    hashes = dict(target.hashes)
    audit_policy_hash = hashes.pop("policy", None)
    if not isinstance(audit_policy_hash, str):
        raise AuditError(f"{target.skill}: hash payload is missing policy hash")
    record = {
        "skill": target.skill,
        "timestamp": timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
        "audit_policy_hash": audit_policy_hash,
        "git_commit": source.get("commit"),
        "source": dict(source),
        "checks": {
            "mechanical": [
                {"name": result.name, "passed": result.passed}
                for result in mechanical_checks
            ],
            "semantic": {
                "passed": not semantic_results,
                "findings": [finding.as_payload() for finding in semantic_results],
            },
        },
        "hashes": hashes,
    }
    return attach_record_digest(record)


def _json_bytes(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _semantic_check(findings: Sequence[Finding]) -> dict[str, object]:
    return {
        "id": "semantic-exactness",
        "version": 1,
        "passed": not findings,
        "findings": [finding.as_payload() for finding in findings],
    }


def _checks_pass(checks: Sequence[Mapping[str, object]]) -> bool:
    return all(check.get("passed") is True for check in checks)


def _policy_input_paths(
    repo_root: Path,
    schema_root: Path,
) -> tuple[tuple[Path, ...], tuple[str, ...]]:
    paths = {
        path
        for path in schema_root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    paths.update(schema_root / name for name in REQUIRED_SCHEMA_INPUTS)
    reasons: set[str] = set()
    manifest_path = repo_root / "skills" / "skill-drift" / "references" / "policy-hash-roots.json"
    paths.add(manifest_path)
    patterns: list[str] = []
    if manifest_path.is_symlink():
        reasons.add("unsafe-policy-manifest")
    else:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            reasons.add("invalid-policy-manifest")
        else:
            if isinstance(manifest, list) and all(isinstance(item, str) for item in manifest):
                patterns = manifest
            else:
                reasons.add("invalid-policy-manifest")
    for pattern in patterns:
        pattern_path = Path(pattern)
        if pattern_path.is_absolute() or ".." in pattern_path.parts:
            reasons.add(f"invalid-policy-pattern:{pattern}")
            continue
        if any(char in pattern for char in "*?[]"):
            matches = sorted(repo_root.glob(pattern))
            if not matches:
                reasons.add(f"missing-policy-input:{pattern}")
        else:
            matches = [repo_root / pattern]
        for match in matches:
            if match.is_symlink() or not match.is_dir():
                paths.add(match)
                continue
            children = [
                child
                for child in sorted(match.rglob("*"))
                if child.is_file() or child.is_symlink()
            ]
            if not children:
                reasons.add(f"missing-policy-input:{pattern}")
            paths.update(children)
    for relative in (
        "skills/skill-audit/_rtx/_audit_certifier.py",
        "src/officina/common/artifact_health.py",
        "src/officina/common/atomic_files.py",
        "src/officina/common/audit_records.py",
        "src/officina/common/blueprint_graph.py",
        "src/officina/common/blueprint_template.py",
        "src/officina/common/git_provenance.py",
        "src/officina/common/pooled_blueprint.py",
    ):
        paths.add(repo_root / relative)
    return tuple(sorted(paths)), tuple(sorted(reasons))


def _expected_file_hashes(
    snapshot: GitSnapshot | None,
    paths: Sequence[Path],
) -> dict[str, str]:
    if snapshot is None:
        return {}
    expected: dict[str, str] = {}
    for path in paths:
        absolute = Path(os.path.abspath(path))
        try:
            relative = absolute.relative_to(snapshot.repo_root).as_posix()
            metadata = path.lstat()
        except (FileNotFoundError, ValueError):
            continue
        if stat.S_ISREG(metadata.st_mode):
            expected[relative] = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    return expected


def _policy_evidence(
    snapshot: GitSnapshot | None,
    repo_root: Path,
    schema_root: Path,
) -> CommandResult:
    paths, manifest_reasons = _policy_input_paths(repo_root, schema_root)
    readiness = check_commit_readiness(
        snapshot,
        paths,
        _expected_file_hashes(snapshot, paths),
    )
    reasons = tuple(sorted({*manifest_reasons, *readiness.reasons}))
    return CommandResult(
        name="policy-readiness",
        command=["commit-readiness", "policy-bundle"],
        exit_code=0 if readiness.stamp_worthy and not reasons else 1,
        stdout="",
        stderr="\n".join(reasons),
    )


def _policy_is_ready(context: AuditContext) -> bool:
    return any(
        evidence.name == "policy-readiness" and evidence.passed
        for evidence in context.raw_evidence
    )


def _key_is_ready(context: AuditContext) -> bool:
    return any(
        evidence.name == "key-readiness" and evidence.passed
        for evidence in context.raw_evidence
    )


def _key_reasons(context: AuditContext) -> tuple[str, ...]:
    return tuple(
        line
        for evidence in context.raw_evidence
        if evidence.name == "key-readiness" and not evidence.passed
        for line in evidence.stderr.splitlines()
        if line
    ) or ("key-not-ready",)


def _read_graph_records(graph: SkillBlueprintGraph) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for node_id in health_node_ids(graph):
        node = graph.nodes[node_id]
        path = health_path_for_node(node)
        if path.is_symlink():
            continue
        try:
            metadata = path.lstat()
            if not stat.S_ISREG(metadata.st_mode):
                continue
            value = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            continue
        if isinstance(value, dict):
            records[node_id] = value
    return records


def _passing_checks(context: AuditContext) -> dict[str, tuple[dict[str, object], ...]]:
    return {
        node_id: checks if _checks_pass(checks) else ()
        for node_id, checks in context.node_checks.items()
    }


def _current_states(context: AuditContext) -> dict[str, Any]:
    return compute_node_hash_states(
        context.graph,
        policy_hash=context.policy_hash,
        schema_hash=context.schema_hash,
        checks_by_node=_passing_checks(context),
        schema_root=context.schema_root,
        certifier={"interface": "skill-audit.machine.certify", "version": 1},
    )


def check_graph_health_from_disk(context: AuditContext) -> GraphHealthReport:
    records = _read_graph_records(context.graph)
    report = check_graph_health(
        context.graph,
        records,
        context.policy_hash,
        context.schema_hash,
        context.key,
        context.schema_root,
    )
    states = _current_states(context)
    statuses: dict[str, NodeHealthStatus] = {}
    for node_id, status in report.nodes.items():
        concerns = list(status.concerns)
        checks_stale = False
        record = records.get(node_id)
        expected_checks = list(context.node_checks.get(node_id, ()))
        if record is not None and record.get("checks") != expected_checks:
            concerns.append("checks-stale")
            checks_stale = True
        if not _checks_pass(context.node_checks.get(node_id, ())):
            concerns.append("checks-stale")
            checks_stale = True
        concerns = list(dict.fromkeys(concerns))
        statuses[node_id] = NodeHealthStatus(
            node_id=node_id,
            healthy=status.healthy and not checks_stale,
            concerns=tuple(concerns),
            expected_certified_health_hash=states[node_id].certified_health_hash,
            recorded_certified_health_hash=status.recorded_certified_health_hash,
            admitted_record_hash=status.admitted_record_hash,
        )
    root = statuses[report.root_id]
    return GraphHealthReport(report.root_id, root.healthy, statuses)


def _health_status(status: NodeHealthStatus, semantic_status: str) -> str:
    if semantic_status == "failed":
        return "unhealthy"
    if status.healthy:
        return "healthy"
    if "missing-health-record" in status.concerns:
        return "unstamped"
    return "refresh-required"


def _node_is_current(status: NodeHealthStatus) -> bool:
    return (
        status.healthy
        and status.recorded_certified_health_hash
        == status.expected_certified_health_hash
        and not node_requires_refresh(status)
    )


def _child_ids(graph: SkillBlueprintGraph, node_id: str) -> tuple[str, ...]:
    return tuple(
        sorted(edge.target_id for edge in health_edges(graph) if edge.source_id == node_id)
    )


def audit_and_maybe_stamp_node(
    context: AuditContext,
    node_id: str,
    outcomes: Mapping[str, NodeAuditOutcome],
) -> NodeAuditOutcome:
    report = check_graph_health_from_disk(context)
    status = report.nodes[node_id]
    checks = context.node_checks.get(node_id, ())
    semantic_status = "passed" if _checks_pass(checks) else "failed"
    record_path = health_path_for_node(context.graph.nodes[node_id])
    health_status = _health_status(status, semantic_status)
    if semantic_status == "failed":
        reason_set = {
            f"semantic:{finding.get('kind', 'finding')}"
            for check in checks
            for finding in check.get("findings", [])
            if isinstance(finding, dict)
        } or {"semantic-check-failed"}
        if not _key_is_ready(context):
            reason_set.update(_key_reasons(context))
        reasons = tuple(sorted(reason_set))
        return NodeAuditOutcome(
            node_id, semantic_status, health_status, False, "not-written", reasons, record_path
        )

    readiness_reasons: list[str] = []
    if not _policy_is_ready(context):
        readiness_reasons.append("policy-not-commit-backed")
    if not _key_is_ready(context):
        readiness_reasons.extend(_key_reasons(context))
    if readiness_reasons:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            tuple(dict.fromkeys(readiness_reasons)),
            record_path,
        )
    unavailable_children = [
        child_id
        for child_id in _child_ids(context.graph, node_id)
        if child_id not in outcomes
        or outcomes[child_id].stamp_status not in {"reused", "written"}
        or outcomes[child_id].health_status != "healthy"
        or not _node_is_current(report.nodes[child_id])
    ]
    if unavailable_children:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            tuple(f"child-not-current:{child_id}" for child_id in unavailable_children),
            record_path,
        )
    if not snapshot_head_matches(context.snapshot):
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            ("head-changed",),
            record_path,
        )

    input_paths = local_input_paths_for_node(context.graph.nodes[node_id])
    expected_hashes = _expected_file_hashes(context.snapshot, input_paths)
    readiness = check_commit_readiness(
        context.snapshot,
        input_paths,
        expected_hashes,
    )
    if not readiness.stamp_worthy or readiness.source is None:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            health_status,
            False,
            "not-written",
            readiness.reasons,
            record_path,
        )
    try:
        record = build_node_health_record(
            context.graph,
            node_id,
            _current_states(context),
            source=readiness.source,
            checks=checks,
            key=context.key,
            certified_at=_certified_at(context),
            schema_root=context.schema_root,
        )
        final_readiness = check_commit_readiness(
            context.snapshot,
            input_paths,
            expected_hashes,
        )
        if not final_readiness.stamp_worthy:
            return NodeAuditOutcome(
                node_id,
                semantic_status,
                health_status,
                False,
                "not-written",
                final_readiness.reasons,
                record_path,
            )
        if not snapshot_head_matches(context.snapshot):
            return NodeAuditOutcome(
                node_id,
                semantic_status,
                health_status,
                False,
                "not-written",
                ("head-changed",),
                record_path,
            )
        atomic_replace_bytes(
            record_path,
            _json_bytes(record),
            allowed_root=context.graph.nodes[node_id].skill_root,
            mode=0o600,
        )
        written_status = check_graph_health_from_disk(context).nodes[node_id]
        if not _node_is_current(written_status):
            raise AuditError(f"post-write node verification failed for {node_id}")
    except (OSError, TypeError, ValueError, AuditError) as exc:
        return NodeAuditOutcome(
            node_id,
            semantic_status,
            "unhealthy",
            True,
            "failed",
            (str(exc),),
            record_path,
        )
    return NodeAuditOutcome(
        node_id, semantic_status, "healthy", True, "written", (), record_path
    )


def _finish_pool(context: AuditContext, report: GraphHealthReport) -> str:
    if not report.healthy:
        return "not-written"
    records = _read_graph_records(context.graph)
    if set(records) != set(health_node_ids(context.graph)):
        return "not-written"
    pool_path = pooled_review_path(context.graph.skill_root)
    pool_health_path = pooled_review_health_path(context.graph.skill_root)
    if pool_path.is_symlink() or pool_health_path.is_symlink():
        return "failed"
    rendered = render_pooled_review(context.graph, records).encode("utf-8")
    try:
        current = check_pooled_review(
            pool_path,
            pool_health_path,
            report,
            context.key,
            graph=context.graph,
            records=records,
            schema_root=context.schema_root,
        )
        if current.healthy and pool_path.read_bytes() == rendered:
            return "reused"
        atomic_replace_bytes(
            pool_path,
            rendered,
            allowed_root=context.graph.skill_root,
            mode=0o600,
        )
        root_record = records[context.graph.root.node_id]
        certification = root_record.get("certification", {})
        certified_at = certification.get("certified_at") if isinstance(certification, dict) else None
        if not isinstance(certified_at, str):
            raise AuditError("root health record has no certification timestamp")
        pool_record = certify_pooled_review(
            pool_path,
            root_record,
            key=context.key,
            certified_at=certified_at,
        )
        atomic_replace_bytes(
            pool_health_path,
            _json_bytes(pool_record),
            allowed_root=context.graph.skill_root,
            mode=0o600,
        )
        verified = check_pooled_review(
            pool_path,
            pool_health_path,
            report,
            context.key,
            graph=context.graph,
            records=records,
            schema_root=context.schema_root,
        )
        return "written" if verified.healthy else "failed"
    except (OSError, TypeError, ValueError, AuditError):
        return "failed"


def finish_root_and_pool(
    context: AuditContext,
    outcomes: Mapping[str, NodeAuditOutcome],
) -> AuditOutcome:
    report = check_graph_health_from_disk(context)
    reconciled = dict(outcomes)
    graph_current: dict[str, bool] = {}
    for node_id in health_postorder_node_ids(context.graph):
        status = report.nodes[node_id]
        graph_current[node_id] = _node_is_current(status) and all(
            graph_current[child_id] for child_id in _child_ids(context.graph, node_id)
        )
        outcome = outcomes[node_id]
        if outcome.stamp_status == "reused" and not graph_current[node_id]:
            reconciled[node_id] = replace(
                outcome,
                health_status=_health_status(status, outcome.semantic_status),
                stamp_worthy=False,
                stamp_status="not-written",
                reasons=tuple(
                    dict.fromkeys((*outcome.reasons, "health-changed-before-finalization"))
                ),
            )
    root_id = context.graph.root.node_id
    root = reconciled[root_id]
    root_status = report.nodes[root_id]
    if root.stamp_status == "written" and not graph_current[root_id]:
        root = replace(
            root,
            health_status=_health_status(root_status, root.semantic_status),
            stamp_worthy=False,
            stamp_status="not-written",
            reasons=tuple(dict.fromkeys((*root.reasons, "health-changed-before-finalization"))),
        )
        reconciled[root_id] = root
    ordered = tuple(
        reconciled[node_id] for node_id in health_postorder_node_ids(context.graph)
    )
    stamp_status = "failed" if any(node.stamp_status == "failed" for node in ordered) else root.stamp_status
    pool_status = _finish_pool(context, report)
    if pool_status not in POOL_STATUSES:
        raise AuditError(f"invalid pool status: {pool_status}")
    return AuditOutcome(
        skill=context.graph.root.node_id,
        source="path",
        skill_root=context.graph.skill_root,
        semantic_status=(
            "passed" if all(node.semantic_status == "passed" for node in ordered) else "failed"
        ),
        stamp_worthy=root.stamp_worthy,
        stamp_status=stamp_status,
        nodes=ordered,
        pool_status=pool_status,
    )


def _semantic_only_typed_outcome(context: AuditContext) -> AuditOutcome:
    outcomes = []
    for node_id in health_postorder_node_ids(context.graph):
        checks = context.node_checks.get(node_id, ())
        semantic_status = "passed" if _checks_pass(checks) else "failed"
        reason_list = (
            ["policy-not-commit-backed"]
            if semantic_status == "passed"
            else ["semantic-check-failed"]
        )
        if not _key_is_ready(context):
            reason_list.extend(_key_reasons(context))
        reasons = tuple(dict.fromkeys(reason_list))
        outcomes.append(
            NodeAuditOutcome(
                node_id=node_id,
                semantic_status=semantic_status,
                health_status="unstamped" if semantic_status == "passed" else "unhealthy",
                stamp_worthy=False,
                stamp_status="not-written",
                reasons=reasons,
                record_path=health_path_for_node(context.graph.nodes[node_id]),
            )
        )
    return AuditOutcome(
        skill=context.graph.root.node_id,
        source="path",
        skill_root=context.graph.skill_root,
        semantic_status=(
            "passed" if all(node.semantic_status == "passed" for node in outcomes) else "failed"
        ),
        stamp_worthy=False,
        stamp_status="not-written",
        nodes=tuple(outcomes),
        pool_status="not-written",
    )


def audit_typed_graph(context: AuditContext) -> AuditOutcome:
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (context.schema_root / name for name in REQUIRED_SCHEMA_INPUTS)
    ):
        return _semantic_only_typed_outcome(context)
    try:
        report = check_graph_health_from_disk(context)
    except (OSError, TypeError, ValueError):
        if not _policy_is_ready(context):
            return _semantic_only_typed_outcome(context)
        raise
    outcomes: dict[str, NodeAuditOutcome] = {}
    for node_id in health_postorder_node_ids(context.graph):
        status = report.nodes[node_id]
        checks = context.node_checks.get(node_id, ())
        if _node_is_current(status) and _checks_pass(checks):
            outcomes[node_id] = NodeAuditOutcome(
                node_id=node_id,
                semantic_status="passed",
                health_status="healthy",
                stamp_worthy=True,
                stamp_status="reused",
                reasons=(),
                record_path=health_path_for_node(context.graph.nodes[node_id]),
            )
            continue
        outcomes[node_id] = audit_and_maybe_stamp_node(context, node_id, outcomes)
    return finish_root_and_pool(context, outcomes)


def _certified_at(context: AuditContext) -> str:
    for evidence in context.raw_evidence:
        if evidence.name == "certification-time" and evidence.stdout:
            return evidence.stdout
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _make_audit_context(
    target: TargetHash,
    mechanical: Sequence[CommandResult],
    timestamp: str | None,
) -> AuditContext:
    schema_root = target.package_root / "references" / "blueprint"
    graph = load_validated_skill_blueprint_graph(target.skill_root, schema_root)
    findings = semantic_findings(target)
    policy_hash = target.hashes.get("policy")
    if not isinstance(policy_hash, str):
        raise AuditError(f"{target.skill}: hash payload is missing policy hash")
    try:
        schema_hash = blueprint_schema_hash(schema_root)
    except (OSError, ValueError):
        schema_hash = "sha256:" + "0" * 64
    snapshot = capture_git_snapshot(target.package_root)
    policy = _policy_evidence(snapshot, target.package_root, schema_root)
    key_root = target.package_root / "skills" / "skill-audit"
    key_path = key_root / ".health-authentication-key"
    key_readiness = CommandResult(
        name="key-readiness",
        command=["load-or-create-hmac-key", key_path.as_posix()],
        exit_code=0,
        stdout="",
        stderr="",
    )
    unsafe_component = next(
        (
            path
            for path in (target.package_root / "skills", key_root, key_path)
            if path.is_symlink()
        ),
        None,
    )
    if unsafe_component is not None:
        key = b"\0" * 32
        key_readiness = replace(
            key_readiness,
            exit_code=1,
            stderr=f"key-unavailable:{unsafe_component}: unsafe symlink key path component",
        )
    elif policy.passed or key_path.exists():
        try:
            key = load_or_create_hmac_key(key_path, allowed_root=key_root)
        except (OSError, ValueError) as exc:
            key = b"\0" * 32
            key_readiness = replace(
                key_readiness,
                exit_code=1,
                stderr=f"key-unavailable:{exc}",
            )
    else:
        key = b"\0" * 32
    node_checks = {node_id: () for node_id in health_node_ids(graph)}
    node_checks[graph.root.node_id] = (_semantic_check(findings),)
    evidence = [*mechanical, policy, key_readiness]
    evidence.append(
        CommandResult(
            name="certification-time",
            command=[],
            exit_code=0,
            stdout=timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
            stderr="",
        )
    )
    return AuditContext(
        graph=graph,
        repo_root=target.package_root,
        schema_root=schema_root,
        policy_hash=policy_hash,
        schema_hash=schema_hash,
        key=key,
        snapshot=snapshot,
        node_checks=node_checks,
        raw_evidence=tuple(evidence),
    )


def _verify_written_nodes(context: AuditContext, outcome: AuditOutcome) -> None:
    report = check_graph_health_from_disk(context)
    for node in outcome.nodes:
        if node.stamp_status != "written":
            continue
        status = report.nodes.get(node.node_id)
        if status is None or not _node_is_current(status):
            raise AuditError(f"post-write node verification failed for {node.node_id}")


def _mark_failed(outcome: AuditOutcome, message: str) -> AuditOutcome:
    root_id = outcome.skill
    nodes = tuple(
        replace(
            node,
            stamp_status="failed",
            reasons=tuple(dict.fromkeys((*node.reasons, message))),
        )
        if node.node_id == root_id
        else node
        for node in outcome.nodes
    )
    return replace(outcome, stamp_status="failed", nodes=nodes)


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
    if (
        not isinstance(skills, list)
        or len(skills) != 1
        or not isinstance(skills[0], dict)
        or skills[0].get("skill") != target.skill
    ):
        raise AuditError("drift-status did not return the exact requested skill")
    if skills[0].get("derived_status") != "audit-current":
        raise AuditError(f"post-write drift verification failed for {target.skill}")


def _legacy_input_paths(skill_root: Path) -> tuple[Path, ...]:
    excluded_names = {
        AUDIT_RECORD_NAME,
        ".pooled-blueprint-review.yaml",
        ".pooled-blueprint-review.health.json",
        ".health-authentication-key",
    }
    paths: list[Path] = []
    for path in sorted(skill_root.rglob("*")):
        if "__pycache__" in path.parts or path.name in excluded_names:
            continue
        if path.name.endswith(".health.json"):
            continue
        if path.is_file() or path.is_symlink():
            paths.append(path)
    return tuple(paths)


def _legacy_record_is_current(
    path: Path,
    target: TargetHash,
    findings: Sequence[Finding],
) -> bool:
    if findings or path.is_symlink():
        return False
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return False
    if not isinstance(record, dict):
        return False
    try:
        digest_matches = record_digest_matches(record)
    except (TypeError, ValueError):
        return False
    if not digest_matches:
        return False
    hashes = dict(target.hashes)
    policy_hash = hashes.pop("policy", None)
    return (
        record.get("skill") == target.skill
        and record.get("audit_policy_hash") == policy_hash
        and record.get("hashes") == hashes
        and record.get("checks", {}).get("semantic", {}).get("passed") is True
    )


def _legacy_outcome(
    target: TargetHash,
    *,
    semantic_status: str,
    stamp_worthy: bool,
    stamp_status: str,
    health_status: str,
    reasons: tuple[str, ...],
) -> AuditOutcome:
    path = target.skill_root / AUDIT_RECORD_NAME
    node = NodeAuditOutcome(
        node_id=target.skill,
        semantic_status=semantic_status,
        health_status=health_status,
        stamp_worthy=stamp_worthy,
        stamp_status=stamp_status,
        reasons=reasons,
        record_path=path,
    )
    return AuditOutcome(
        skill=target.skill,
        source=target.source,
        skill_root=target.skill_root,
        semantic_status=semantic_status,
        stamp_worthy=stamp_worthy,
        stamp_status=stamp_status,
        nodes=(node,),
        pool_status="not-written",
    )


def _audit_legacy_target(
    target: TargetHash,
    mechanical: Sequence[CommandResult],
    timestamp: str | None,
) -> tuple[AuditOutcome, tuple[CommandResult, ...]]:
    findings = semantic_findings(target)
    semantic_status = "passed" if not findings else "failed"
    record_path = target.skill_root / AUDIT_RECORD_NAME
    snapshot = capture_git_snapshot(target.package_root)
    schema_root = target.package_root / "references" / "blueprint"
    policy = _policy_evidence(snapshot, target.package_root, schema_root)
    evidence = tuple([*mechanical, policy])
    if _legacy_record_is_current(record_path, target, findings):
        return (
            _legacy_outcome(
                target,
                semantic_status="passed",
                stamp_worthy=True,
                stamp_status="reused",
                health_status="healthy",
                reasons=(),
            ),
            evidence,
        )
    if findings:
        reasons = tuple(f"semantic:{finding.kind}" for finding in findings)
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unhealthy",
                reasons=reasons,
            ),
            evidence,
        )
    if not policy.passed:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=("policy-not-commit-backed",),
            ),
            evidence,
        )
    if not snapshot_head_matches(snapshot):
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=("head-changed",),
            ),
            evidence,
        )
    input_paths = _legacy_input_paths(target.skill_root)
    expected_hashes = _expected_file_hashes(snapshot, input_paths)
    readiness = check_commit_readiness(
        snapshot,
        input_paths,
        expected_hashes,
    )
    if not readiness.stamp_worthy or readiness.source is None:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=readiness.reasons,
            ),
            evidence,
        )
    record = build_record(
        target,
        mechanical_checks=mechanical,
        semantic_results=findings,
        source=readiness.source,
        timestamp=timestamp,
    )
    final_readiness = check_commit_readiness(
        snapshot,
        input_paths,
        expected_hashes,
    )
    if not final_readiness.stamp_worthy:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=final_readiness.reasons,
            ),
            evidence,
        )
    if not snapshot_head_matches(snapshot):
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=False,
                stamp_status="not-written",
                health_status="unstamped",
                reasons=("head-changed",),
            ),
            evidence,
        )
    try:
        atomic_replace_bytes(
            record_path,
            _json_bytes(record),
            allowed_root=target.skill_root,
            mode=0o600,
        )
    except (OSError, TypeError, ValueError) as exc:
        return (
            _legacy_outcome(
                target,
                semantic_status=semantic_status,
                stamp_worthy=True,
                stamp_status="failed",
                health_status="unhealthy",
                reasons=(str(exc),),
            ),
            evidence,
        )
    return (
        _legacy_outcome(
            target,
            semantic_status=semantic_status,
            stamp_worthy=True,
            stamp_status="written",
            health_status="healthy",
            reasons=(),
        ),
        evidence,
    )


def _failure_outcome(target: TargetHash, message: str) -> AuditOutcome:
    return _legacy_outcome(
        target,
        semantic_status="failed",
        stamp_worthy=False,
        stamp_status="failed",
        health_status="unhealthy",
        reasons=(message,),
    )


def _target_for_failed_request(request: str, repo_root: Path) -> TargetHash:
    path = Path(request).expanduser()
    skill_root = path.resolve(strict=False) if is_path_like(request) else repo_root / "skills" / request
    return TargetHash(
        skill=skill_root.name,
        source="path" if is_path_like(request) else "name",
        package_root=repo_root,
        skills_root=skill_root.parent,
        skill_root=skill_root,
        hashes={},
    )


def _audit_target(
    dispatcher: Dispatcher,
    target: TargetHash,
    mechanical: Sequence[CommandResult],
    timestamp: str | None,
) -> tuple[AuditOutcome, tuple[CommandResult, ...]]:
    try:
        typed = load_blueprint(target.skill_root).get("schema_version") == 2
        context: AuditContext | None = None
        if typed:
            context = _make_audit_context(target, mechanical, timestamp)
            outcome = replace(audit_typed_graph(context), source=target.source)
            evidence = context.raw_evidence
        else:
            outcome, evidence = _audit_legacy_target(target, mechanical, timestamp)
        if any(node.stamp_status == "written" for node in outcome.nodes):
            try:
                if context is not None:
                    _verify_written_nodes(context, outcome)
                root = next(node for node in outcome.nodes if node.node_id == outcome.skill)
                if root.stamp_status == "written":
                    verify_post_write(dispatcher, target)
            except (OSError, TypeError, ValueError, AuditError) as exc:
                return _mark_failed(outcome, str(exc)), evidence
        return outcome, evidence
    except (OSError, TypeError, ValueError, AuditError) as exc:
        return _failure_outcome(target, str(exc)), tuple(mechanical)


def certify(
    dispatcher: Dispatcher,
    *,
    targets: Sequence[str],
    repo_root: Path = REPO_ROOT,
    skip_mechanical: bool = False,
    timestamp: str | None = None,
) -> tuple[list[CommandResult], list[AuditOutcome]]:
    mechanical = [] if skip_mechanical else run_mechanical_checks(dispatcher, repo_root=repo_root)
    evidence: list[CommandResult] = list(mechanical)
    outcomes: list[AuditOutcome] = []
    seen: set[tuple[str, str]] = set()

    if targets:
        for request in targets:
            try:
                target = collect_exact_target(dispatcher, request)
            except AuditError as exc:
                outcomes.append(_failure_outcome(_target_for_failed_request(request, repo_root), str(exc)))
                continue
            identity = (target.skill, target.skill_root.resolve(strict=False).as_posix())
            if identity in seen:
                continue
            seen.add(identity)
            outcome, target_evidence = _audit_target(
                dispatcher,
                target,
                mechanical,
                timestamp,
            )
            outcomes.append(outcome)
            for item in target_evidence:
                if item not in evidence:
                    evidence.append(item)
        return evidence, outcomes
    else:
        groups = [(None, collect_targets(dispatcher, ()))]

    for _request, resolved in groups:
        for target in resolved:
            identity = (target.skill, target.skill_root.resolve(strict=False).as_posix())
            if identity in seen:
                continue
            seen.add(identity)
            outcome, target_evidence = _audit_target(
                dispatcher,
                target,
                mechanical,
                timestamp,
            )
            outcomes.append(outcome)
            for item in target_evidence:
                if item not in evidence:
                    evidence.append(item)
    return evidence, outcomes


def render_text(outcomes: Sequence[AuditOutcome]) -> str:
    lines = [
        "# Skill Audit Report",
        "",
        "| Source | Skill | Semantic | Stamp | Pool |",
        "|---|---|---|---|---|",
    ]
    for outcome in outcomes:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(outcome.source),
                    markdown_cell(outcome.skill),
                    markdown_cell(outcome.semantic_status),
                    markdown_cell(outcome.stamp_status),
                    markdown_cell(outcome.pool_status),
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
    parser.add_argument("--timestamp", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None, dispatcher: Dispatcher | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    runtime = dispatcher or Interface()
    try:
        evidence, outcomes = certify(
            runtime,
            targets=args.targets,
            skip_mechanical=args.skip_mechanical,
            timestamp=args.timestamp,
        )
    except AuditError as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2

    certified = [
        {**outcome.as_payload(), "status": "audit-current"}
        for outcome in outcomes
        if outcome.semantic_status == "passed"
        and outcome.stamp_status in {"reused", "written"}
    ]
    not_written = [
        outcome.as_payload()
        for outcome in outcomes
        if outcome.semantic_status == "passed" and outcome.stamp_status == "not-written"
    ]
    failed = [
        outcome.as_payload()
        for outcome in outcomes
        if outcome.semantic_status == "failed" or outcome.stamp_status == "failed"
    ]
    payload = {
        "ok": not failed,
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "evidence": [result.as_payload() for result in evidence],
        "certified": certified,
        "not_written": not_written,
        "failed": failed,
    }
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(render_text(outcomes), end="")
    return 2 if failed else 0


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
