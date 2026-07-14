#!/usr/bin/env python3
"""Skill drift status reporter.

The checker compares installed skills against their local audit records. JSON
mode writes only to stdout; the default human-readable mode also saves a
timestamped Markdown report under the skill's ignored ``_build`` directory.

The audit signal and health signal are intentionally separate:

- audit status answers whether the readable audit record still matches the
  certified artifact and audit standards;
- optional health checks answer whether repo validators and skill tests pass
  right now.

When health checks are requested, ``overall_status`` is the OR of those two
conditions: a stale audit or a failed health check both require attention. A
health failure does not rewrite or reinterpret the audit record as stale.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Sequence

import jsonschema

RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from _drift_hashes import HashEntry, digest_entries, entries_for_path, hash_interface, hash_skill
from _skill_sources import SkillSource, observed_skill_sources
from officina.common.artifact_health import (
    GraphHealthReport,
    NodeHashState,
    blueprint_schema_hash,
    check_graph_health,
    compute_node_hash_states,
    health_path_for_node,
)
from officina.common.audit_records import (
    HMAC_KEY_BYTES,
    RECORD_DIGEST_FIELD,
    record_digest_matches,
)
from officina.common.blueprint_graph import (
    SkillBlueprintGraph,
    load_validated_skill_blueprint_graph,
)
from officina.common.pooled_blueprint import (
    check_pooled_review,
    pooled_review_health_path,
    pooled_review_path,
)
from officina.blueprint_search import BlueprintSearchError, load_blueprint_record
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = RTX_DIR.parent
BUILD_DIR = SKILL_ROOT / "_build"
AUDIT_RECORD_NAME = ".last_audit.json"
OUTPUT_SCHEMA_VERSION = 1


class DriftCheckError(RuntimeError):
    """Raised when a requested skill cannot be checked."""


class PooledReviewSnapshotError(DriftCheckError):
    """Raised when a non-authoritative pooled input cannot be snapshotted."""

    def __init__(self, concern_kind: str, message: str) -> None:
        super().__init__(message)
        self.concern_kind = concern_kind


_REQUIRED_TARGET_SCHEMA_INPUTS = {
    "behavior-source.schema.json",
    "common.schema.json",
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
}


def _require_descriptor_safe_reads() -> None:
    if (
        os.name != "posix"
        or not getattr(os, "O_DIRECTORY", 0)
        or not getattr(os, "O_NOFOLLOW", 0)
        or os.open not in getattr(os, "supports_dir_fd", set())
    ):
        raise DriftCheckError("descriptor-safe target reads are unavailable")


def _target_relative_path(package_root: Path, path: Path) -> tuple[Path, Path]:
    root = Path(os.path.abspath(package_root))
    target = Path(os.path.abspath(path))
    try:
        relative = target.relative_to(root)
    except ValueError as exc:
        raise DriftCheckError(f"target path is outside selected package: {path}") from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise DriftCheckError(f"invalid target-relative path: {path}")
    return root, relative


def _secure_open_target(package_root: Path, path: Path, *, directory: bool) -> int:
    _require_descriptor_safe_reads()
    root, relative = _target_relative_path(package_root, path)
    file_flags = (
        os.O_RDONLY
        | os.O_NOFOLLOW
        | os.O_NONBLOCK
        | getattr(os, "O_CLOEXEC", 0)
    )
    directory_flags = file_flags | os.O_DIRECTORY
    directory_fd = -1
    try:
        directory_fd = os.open(root, directory_flags)
        for component in relative.parts[:-1]:
            next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
            if not stat.S_ISDIR(os.fstat(next_fd).st_mode):
                os.close(next_fd)
                raise DriftCheckError(
                    f"unsafe target path (symbolic link or non-directory component): {path}"
                )
            os.close(directory_fd)
            directory_fd = next_fd
        flags = directory_flags if directory else file_flags
        descriptor = os.open(relative.parts[-1], flags, dir_fd=directory_fd)
        metadata = os.fstat(descriptor)
        expected = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        if not expected:
            os.close(descriptor)
            noun = "directory" if directory else "regular file"
            raise DriftCheckError(f"target path must be a non-symlink {noun}: {path}")
        return descriptor
    except FileNotFoundError:
        raise
    except DriftCheckError:
        raise
    except OSError as exc:
        raise DriftCheckError(
            f"unsafe target path (symbolic link or non-regular component): {path}"
        ) from exc
    finally:
        if directory_fd >= 0:
            os.close(directory_fd)


def secure_read_target_file(package_root: Path, path: Path) -> bytes:
    descriptor = _secure_open_target(package_root, path, directory=False)
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 1024 * 1024):
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def secure_list_target_directory(package_root: Path, path: Path) -> tuple[str, ...]:
    descriptor = _secure_open_target(package_root, path, directory=True)
    try:
        return tuple(sorted(os.listdir(descriptor)))
    finally:
        os.close(descriptor)


@contextmanager
def secure_schema_snapshot(package_root: Path) -> Iterator[Path]:
    target_root = package_root / "references" / "blueprint"
    names = secure_list_target_directory(package_root, target_root)
    schema_names = _REQUIRED_TARGET_SCHEMA_INPUTS | {
        name for name in names if name.endswith(".schema.json")
    }
    missing = sorted(schema_names - set(names))
    if missing:
        raise DriftCheckError(
            f"{target_root}: missing blueprint schema inputs: {', '.join(missing)}"
        )
    with tempfile.TemporaryDirectory(prefix="skill-drift-schema-") as temporary:
        snapshot = Path(temporary)
        for name in sorted(schema_names):
            data = secure_read_target_file(package_root, target_root / name)
            if name.endswith(".json"):
                try:
                    json.loads(data.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise DriftCheckError(
                        f"invalid target schema file {target_root / name}: {exc}"
                    ) from exc
            (snapshot / name).write_bytes(data)
        yield snapshot


@contextmanager
def secure_pooled_review_snapshot(
    package_root: Path,
    skill_dir: Path,
) -> Iterator[tuple[Path, Path]]:
    originals = (
        pooled_review_path(skill_dir),
        pooled_review_health_path(skill_dir),
    )
    with tempfile.TemporaryDirectory(prefix="skill-drift-pooled-review-") as temporary:
        snapshot = Path(temporary)
        copied: list[Path] = []
        for index, original in enumerate(originals):
            destination = snapshot / original.name
            try:
                data = secure_read_target_file(package_root, original)
            except FileNotFoundError:
                pass
            except (DriftCheckError, OSError) as exc:
                concern_kind = (
                    "invalid-pooled-review"
                    if index == 0
                    else "invalid-pooled-review-health"
                )
                raise PooledReviewSnapshotError(concern_kind, str(exc)) from exc
            else:
                try:
                    destination.write_bytes(data)
                except OSError as exc:
                    concern_kind = (
                        "invalid-pooled-review"
                        if index == 0
                        else "invalid-pooled-review-health"
                    )
                    raise PooledReviewSnapshotError(
                        concern_kind,
                        f"cannot snapshot {original}: {exc}",
                    ) from exc
            copied.append(destination)
        yield copied[0], copied[1]


def secure_load_target_key(package_root: Path, path: Path) -> bytes:
    key = secure_read_target_file(package_root, path)
    if len(key) != HMAC_KEY_BYTES:
        raise ValueError(f"{path}: HMAC key must be exactly {HMAC_KEY_BYTES} bytes")
    return key


def read_target_record(
    package_root: Path,
    path: Path,
) -> tuple[dict[str, Any] | None, Concern | None]:
    try:
        raw_bytes = secure_read_target_file(package_root, path)
    except FileNotFoundError:
        return None, Concern("missing-record", f"{path.as_posix()} does not exist")
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, Concern("corrupt-record", f"{path.as_posix()} is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        return None, Concern("corrupt-record", f"{path.as_posix()} must contain a JSON object")
    return raw, None


@dataclass(frozen=True)
class Concern:
    kind: str
    message: str
    key: str | None = None
    recorded: str | None = None
    current: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "message": self.message}
        if self.key is not None:
            payload["key"] = self.key
        if self.recorded is not None:
            payload["recorded"] = self.recorded
        if self.current is not None:
            payload["current"] = self.current
        return payload


@dataclass(frozen=True)
class HealthCheck:
    name: str
    passed: bool
    command: list[str]
    returncode: int | None = None
    skipped: bool = False
    message: str | None = None
    output: str | None = None

    def as_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "passed": self.passed,
            "skipped": self.skipped,
            "command": self.command,
        }
        if self.returncode is not None:
            payload["returncode"] = self.returncode
        if self.message is not None:
            payload["message"] = self.message
        if self.output:
            payload["output"] = self.output
        return payload


@dataclass(frozen=True)
class SkillDriftReport:
    skill: str
    derived_status: str
    concerns: list[Concern]
    record_path: Path
    current_hashes: dict[str, Any]
    recorded_hashes: dict[str, Any] | None
    source: str
    package_root: Path
    skills_root: Path
    timestamp: str | None = None
    health_checks: list[HealthCheck] | None = None

    @property
    def health_status(self) -> str:
        if self.health_checks is None:
            return "not-run"
        return "health-passed" if all(check.passed for check in self.health_checks) else "health-failed"

    @property
    def overall_status(self) -> str:
        if self.derived_status == "audit-stale" or self.health_status == "health-failed":
            return "needs-attention"
        return "ok"

    def as_payload(self) -> dict[str, Any]:
        payload = {
            "skill": self.skill,
            "source": self.source,
            "derived_status": self.derived_status,
            "concerns": [concern.as_payload() for concern in self.concerns],
            "record_path": display_path(self.record_path, self.package_root),
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "timestamp": self.timestamp,
            "recorded_hashes": self.recorded_hashes,
            "current_hashes": self.current_hashes,
        }
        if self.health_checks is not None:
            payload["health_status"] = self.health_status
            payload["overall_status"] = self.overall_status
            payload["health_checks"] = [check.as_payload() for check in self.health_checks]
        return payload


@dataclass(frozen=True)
class SkillHashReport:
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    hashes: dict[str, Any]

    @classmethod
    def from_graph_report(
        cls,
        source: SkillSource,
        graph: SkillBlueprintGraph,
        report: GraphHealthReport,
        *,
        policy_hash: str,
        schema_hash: str,
        node_states: dict[str, NodeHashState],
    ) -> "SkillHashReport":
        nodes = {
            node_id: {
                "blueprint_type": graph.nodes[node_id].blueprint_type,
                "local_hash": node_states[node_id].local_hash,
                "artifact_graph_hash": node_states[node_id].artifact_graph_hash,
                "expected_certified_health_hash": (
                    report.nodes[node_id].expected_certified_health_hash
                ),
            }
            for node_id in sorted(graph.nodes)
        }
        return cls(
            skill=graph.root.node_id,
            source=source.source,
            package_root=source.package_root,
            skills_root=source.skills_root,
            hashes={
                "policy": policy_hash,
                "schema": schema_hash,
                "nodes": nodes,
            },
        )

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "hashes": self.hashes,
        }


@dataclass(frozen=True)
class SkillHashFailure:
    skill: str
    source: str
    package_root: Path
    skills_root: Path
    message: str

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "error": {
                "kind": "hash-unavailable",
                "message": self.message,
            },
        }


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def observed_skill_names(skills_root: Path) -> list[str]:
    if not skills_root.is_dir():
        return []
    return sorted(
        path.name
        for path in skills_root.iterdir()
        if path.is_dir() and path.name != ".system" and (path / "SKILL.md").is_file()
    )


def blueprint_skill_names(skills_root: Path) -> list[str]:
    """Return observed skills that have a local blueprint contract."""

    return [
        skill_name
        for skill_name in observed_skill_names(skills_root)
        if (skills_root / skill_name / "blueprint.yaml").is_file()
    ]


def skill_dir_for(skills_root: Path, skill_name: str) -> Path:
    skill_dir = skills_root / skill_name
    if not (skill_dir / "SKILL.md").is_file():
        raise DriftCheckError(f"skill `{skill_name}` does not exist or lacks SKILL.md")
    return skill_dir


def is_path_like_target(value: str) -> bool:
    return "/" in value or "\\" in value or value.startswith((".", "~"))


def source_for_skill_root(skill_root: Path, *, source: str = "path") -> SkillSource:
    skill_root = skill_root.expanduser().resolve()
    if not (skill_root / "SKILL.md").is_file():
        raise DriftCheckError(f"{skill_root.as_posix()} does not exist or lacks SKILL.md")
    skills_root = skill_root.parent
    package_root = skills_root.parent if skills_root.name == "skills" else skill_root
    return SkillSource(source=source, package_root=package_root.resolve(), skills_root=skills_root.resolve())


@dataclass(frozen=True)
class RequestedScope:
    source: SkillSource
    skill_names: tuple[str, ...]


def load_blueprint(skill_dir: Path) -> dict[str, Any]:
    path = skill_dir / "blueprint.yaml"
    if not path.is_file():
        raise DriftCheckError(f"{skill_dir.name}: missing blueprint.yaml")
    repo_root = skill_dir.parent.parent if skill_dir.parent.name == "skills" else skill_dir.parent
    try:
        return load_blueprint_record(path, repo_root=repo_root, skill=skill_dir.name).data
    except BlueprintSearchError as exc:
        raise DriftCheckError(str(exc)) from exc


def compute_policy_hash(repo_root: Path) -> str:
    package_root = repo_root.resolve()
    manifest = policy_roots_path(package_root)
    manifest_bytes = secure_read_target_file(package_root, manifest)
    entries = [
        HashEntry(
            display_path(manifest, package_root),
            "file",
            manifest_bytes,
        )
    ]
    for pattern in load_policy_patterns(package_root, manifest_bytes=manifest_bytes):
        relative = Path(pattern)
        if relative.is_absolute() or ".." in relative.parts:
            raise DriftCheckError(f"policy hash root must stay under target package: {pattern}")
        if any(char in pattern for char in "*?[]"):
            paths = sorted(package_root.glob(pattern), key=lambda item: item.as_posix())
        else:
            paths = [package_root / pattern]
        for path in paths:
            try:
                data = secure_read_target_file(package_root, path)
            except FileNotFoundError:
                continue
            entries.append(HashEntry(display_path(path, package_root), "file", data))
    return digest_entries(entries)


def policy_roots_path(package_root: Path) -> Path:
    return package_root / "skills" / "skill-drift" / "references" / "policy-hash-roots.json"


def load_policy_patterns(
    package_root: Path,
    *,
    manifest_bytes: bytes | None = None,
) -> list[str]:
    manifest = policy_roots_path(package_root)
    try:
        data = (
            manifest_bytes
            if manifest_bytes is not None
            else secure_read_target_file(package_root, manifest)
        )
        raw = json.loads(data.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DriftCheckError(f"cannot read target policy manifest {manifest.as_posix()}: {exc}") from exc
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise DriftCheckError(f"{manifest.as_posix()} must contain a JSON string list")
    return raw


def compute_interface_hashes(skill_dir: Path, repo_root: Path, blueprint: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    interfaces = blueprint.get("interfaces")
    if not isinstance(interfaces, dict):
        return hashes
    for namespace in ("llm", "machine"):
        namespace_entries = interfaces.get(namespace)
        if not isinstance(namespace_entries, dict):
            continue
        for interface_name, interface_spec in sorted(namespace_entries.items()):
            if isinstance(interface_spec, dict):
                hashes[f"{namespace}.{interface_name}"] = hash_interface(skill_dir, repo_root, interface_spec)
    return hashes


def compute_audit_hashes(install_root: Path, skills_root: Path, skill_name: str) -> dict[str, Any]:
    skill_dir = skill_dir_for(skills_root, skill_name)
    blueprint = load_blueprint(skill_dir)
    return {
        "skill": hash_skill(skill_dir, install_root, blueprint),
        "policy": compute_policy_hash(install_root),
        "interfaces": compute_interface_hashes(skill_dir, install_root, blueprint),
    }


def read_record(path: Path) -> tuple[dict[str, Any] | None, Concern | None]:
    if not path.exists():
        return None, Concern("missing-record", f"{path.as_posix()} does not exist")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return None, Concern("corrupt-record", f"{path.as_posix()} is not valid JSON: {exc}")
    if not isinstance(raw, dict):
        return None, Concern("corrupt-record", f"{path.as_posix()} must contain a JSON object")
    return raw, None


def validate_record_shape(record: dict[str, Any], skill_name: str) -> list[Concern]:
    concerns: list[Concern] = []
    if not isinstance(record.get("timestamp"), str):
        concerns.append(Concern("corrupt-record", "record is missing string timestamp"))
    if not isinstance(record.get("audit_policy_hash"), str):
        concerns.append(Concern("corrupt-record", "record is missing string audit_policy_hash"))
    if not isinstance(record.get(RECORD_DIGEST_FIELD), str):
        concerns.append(Concern("corrupt-record", f"record is missing string {RECORD_DIGEST_FIELD}"))
    elif not record_digest_matches(record):
        concerns.append(Concern("record-digest-mismatch", "record_digest does not match record contents"))

    record_skill = record.get("skill")
    if not isinstance(record_skill, str):
        concerns.append(Concern("corrupt-record", "record is missing string skill"))
    elif record_skill != skill_name:
        concerns.append(
            Concern(
                "skill-mismatch",
                f"record skill `{record_skill}` does not match `{skill_name}`",
            )
        )

    hashes = record.get("hashes")
    if not isinstance(hashes, dict):
        concerns.append(Concern("corrupt-record", "record is missing hashes object"))
        return concerns
    if "skill" in hashes and not isinstance(hashes["skill"], str):
        concerns.append(Concern("corrupt-record", "hashes.skill must be a string"))
    interfaces = hashes.get("interfaces")
    if "interfaces" in hashes and not (
        isinstance(interfaces, dict)
        and all(isinstance(key, str) and isinstance(value, str) for key, value in interfaces.items())
    ):
        concerns.append(Concern("corrupt-record", "hashes.interfaces must be an object of string hashes"))
    concerns.extend(validate_checks(record.get("checks")))
    return concerns


def validate_checks(checks: Any) -> list[Concern]:
    concerns: list[Concern] = []
    if not isinstance(checks, dict):
        return [Concern("corrupt-record", "record is missing checks object")]
    mechanical = checks.get("mechanical")
    if not isinstance(mechanical, list):
        concerns.append(Concern("corrupt-record", "checks.mechanical must be a list"))
    else:
        for index, result in enumerate(mechanical):
            if not isinstance(result, dict):
                concerns.append(Concern("corrupt-record", f"checks.mechanical[{index}] must be an object"))
                continue
            if result.get("passed") is not True:
                concerns.append(Concern("failed-check", f"mechanical check {index} is not passed"))
    semantic = checks.get("semantic")
    if not isinstance(semantic, dict):
        concerns.append(Concern("corrupt-record", "checks.semantic must be an object"))
    elif semantic.get("passed") is not True:
        concerns.append(Concern("failed-check", "semantic exactness check is not passed"))
    return concerns


def recorded_hashes_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    hashes = record.get("hashes")
    if not isinstance(hashes, dict):
        return None
    recorded = dict(hashes)
    audit_policy_hash = record.get("audit_policy_hash")
    if isinstance(audit_policy_hash, str):
        recorded["policy"] = audit_policy_hash
    return recorded


def flatten_hashes(hashes: dict[str, Any]) -> dict[str, str]:
    flattened: dict[str, str] = {}
    if isinstance(hashes.get("skill"), str):
        flattened["skill"] = hashes["skill"]
    if isinstance(hashes.get("policy"), str):
        flattened["policy"] = hashes["policy"]
    interfaces = hashes.get("interfaces")
    if isinstance(interfaces, dict):
        for name, value in sorted(interfaces.items()):
            if isinstance(name, str) and isinstance(value, str):
                flattened[f"interfaces.{name}"] = value
    return flattened


def compare_hashes(recorded_hashes: dict[str, Any] | None, current_hashes: dict[str, Any]) -> list[Concern]:
    if recorded_hashes is None:
        return []
    concerns: list[Concern] = []
    recorded = flatten_hashes(recorded_hashes)
    current = flatten_hashes(current_hashes)
    for key in sorted(current):
        if key not in recorded:
            concerns.append(Concern("missing-hash", f"record is missing hash `{key}`", key=key, current=current[key]))
        elif recorded[key] != current[key]:
            concerns.append(
                Concern(
                    "changed-hash",
                    f"hash `{key}` changed",
                    key=key,
                    recorded=recorded[key],
                    current=current[key],
                )
            )
    for key in sorted(recorded):
        if key not in current:
            concerns.append(
                Concern(
                    "extra-recorded-hash",
                    f"record contains obsolete hash `{key}`",
                    key=key,
                    recorded=recorded[key],
                )
            )
    return concerns


def _output_tail(stdout: str, stderr: str, *, limit: int = 4000) -> str:
    combined = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    if len(combined) <= limit:
        return combined
    return combined[-limit:]


def _run_health_command(name: str, command: list[str], cwd: Path) -> HealthCheck:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
    )
    return HealthCheck(
        name=name,
        passed=result.returncode == 0,
        command=command,
        returncode=result.returncode,
        output=_output_tail(result.stdout, result.stderr),
    )


def run_validator_health(source: SkillSource) -> HealthCheck:
    """Run repo-level validators as a health signal, not as audit evidence."""

    runner = source.package_root / "validators" / "runner.py"
    command = [sys.executable, runner.as_posix()]
    if source.package_root.resolve() != REPO_ROOT.resolve():
        return HealthCheck(
            name="validators",
            passed=True,
            skipped=True,
            command=command,
            message="copied targets are data-only and cannot run validators",
        )
    if not runner.is_file():
        return HealthCheck(
            name="validators",
            passed=True,
            skipped=True,
            command=command,
            message=f"{display_path(runner, source.package_root)} is not available",
        )
    return _run_health_command("validators", command, source.package_root)


def run_skill_test_health(source: SkillSource, skill_name: str) -> HealthCheck:
    """Run a skill's own tests as a health signal, not as audit evidence."""

    tests_dir = source.skills_root / skill_name / "tests"
    command = [sys.executable, "-m", "pytest", "-q", tests_dir.as_posix()]
    if source.package_root.resolve() != REPO_ROOT.resolve():
        return HealthCheck(
            name="skill-tests",
            passed=True,
            skipped=True,
            command=command,
            message="copied targets are data-only and cannot run skill tests",
        )
    if not tests_dir.is_dir():
        return HealthCheck(
            name="skill-tests",
            passed=True,
            skipped=True,
            command=command,
            message=f"{display_path(tests_dir, source.package_root)} is not available",
        )
    return _run_health_command("skill-tests", command, source.package_root)


def health_checks_for_skill(
    source: SkillSource,
    skill_name: str,
    *,
    validator_health: HealthCheck | None = None,
) -> list[HealthCheck]:
    return [
        validator_health if validator_health is not None else run_validator_health(source),
        run_skill_test_health(source, skill_name),
    ]


def check_typed_skill(
    source: SkillSource,
    skill_name: str,
    *,
    with_test_validate: bool = False,
    validator_health: HealthCheck | None = None,
) -> SkillDriftReport:
    """Check authenticated recursive health for one typed blueprint graph."""

    skill_dir = skill_dir_for(source.skills_root, skill_name)
    record_path = skill_dir / AUDIT_RECORD_NAME
    concerns: list[Concern] = []
    current_hashes: dict[str, Any] = {}
    recorded_hashes: dict[str, Any] | None = None
    timestamp: str | None = None
    canonical_healthy = False

    try:
        with secure_schema_snapshot(source.package_root) as schema_root:
            graph = load_validated_skill_blueprint_graph(skill_dir, schema_root)
            policy_hash = compute_policy_hash(source.package_root)
            schema_hash = blueprint_schema_hash(schema_root)
            key_path = (
                source.package_root
                / "skills"
                / "skill-audit"
                / ".health-authentication-key"
            )
            try:
                key = secure_load_target_key(source.package_root, key_path)
            except FileNotFoundError:
                concerns.append(
                    Concern(
                        "missing-authentication-key",
                        f"{key_path.as_posix()} does not exist",
                    )
                )
                key = None
            except ValueError as exc:
                concerns.append(Concern("invalid-authentication-key", str(exc)))
                key = None

            if key is not None:
                records: dict[str, dict[str, Any]] = {}
                for node_id, node in graph.nodes.items():
                    path = health_path_for_node(node)
                    record, read_concern = read_target_record(source.package_root, path)
                    if read_concern is not None:
                        concerns.append(
                            Concern(
                                read_concern.kind,
                                f"{node_id}: {read_concern.message}",
                                key=node_id,
                            )
                        )
                    elif record is not None:
                        records[node_id] = record

                report = check_graph_health(
                    graph,
                    records,
                    policy_hash=policy_hash,
                    schema_hash=schema_hash,
                    schema_root=schema_root,
                    key=key,
                )
                canonical_healthy = report.healthy
                for node_id in sorted(report.nodes):
                    status = report.nodes[node_id]
                    for kind in status.concerns:
                        concerns.append(
                            Concern(
                                kind,
                                f"{node_id}: {kind.replace('-', ' ')}",
                                key=node_id,
                            )
                        )

                root_status = report.nodes[report.root_id]
                current_hashes = {
                    "policy": policy_hash,
                    "schema": schema_hash,
                    "root_certified_health": (
                        root_status.expected_certified_health_hash
                    ),
                }
                root_record = records.get(report.root_id)
                if (
                    root_status.recorded_certified_health_hash is not None
                    and isinstance(root_record, dict)
                ):
                    hashes = root_record.get("hashes")
                    if isinstance(hashes, dict):
                        recorded_hashes = dict(hashes)
                    certification = root_record.get("certification")
                    if isinstance(certification, dict) and isinstance(
                        certification.get("certified_at"), str
                    ):
                        timestamp = certification["certified_at"]

                try:
                    with secure_pooled_review_snapshot(
                        source.package_root,
                        skill_dir,
                    ) as (pool_path, pool_health_path):
                        pool_report = check_pooled_review(
                            pool_path,
                            pool_health_path,
                            report,
                            key,
                            graph=graph,
                            records=records,
                            schema_root=schema_root,
                        )
                except PooledReviewSnapshotError as exc:
                    concerns.append(Concern(exc.concern_kind, str(exc)))
                except (
                    DriftCheckError,
                    OSError,
                    TypeError,
                    ValueError,
                    KeyError,
                    jsonschema.exceptions.SchemaError,
                ) as exc:
                    concerns.append(
                        Concern(
                            "invalid-pooled-review-health",
                            f"pooled review health cannot be verified: {exc}",
                        )
                    )
                else:
                    for kind in pool_report.concerns:
                        concerns.append(Concern(kind, kind.replace("-", " ")))
    except (
        DriftCheckError,
        OSError,
        TypeError,
        ValueError,
        KeyError,
        jsonschema.exceptions.SchemaError,
    ) as exc:
        canonical_healthy = False
        current_hashes = {}
        recorded_hashes = None
        timestamp = None
        concerns.append(
            Concern("hash-unavailable", f"{skill_name}: typed status unavailable: {exc}")
        )

    return SkillDriftReport(
        skill=skill_name,
        derived_status="audit-current" if canonical_healthy else "audit-stale",
        concerns=concerns,
        record_path=record_path,
        current_hashes=current_hashes,
        recorded_hashes=recorded_hashes,
        source=source.source,
        package_root=source.package_root,
        skills_root=source.skills_root,
        timestamp=timestamp,
        health_checks=(
            health_checks_for_skill(source, skill_name, validator_health=validator_health)
            if with_test_validate
            else None
        ),
    )


def check_skill(
    source: SkillSource,
    skill_name: str,
    *,
    with_test_validate: bool = False,
    validator_health: HealthCheck | None = None,
) -> SkillDriftReport:
    skill_dir = skill_dir_for(source.skills_root, skill_name)
    try:
        typed = load_blueprint(skill_dir).get("schema_version") == 2
    except DriftCheckError:
        typed = False
    if typed:
        return check_typed_skill(
            source,
            skill_name,
            with_test_validate=with_test_validate,
            validator_health=validator_health,
        )
    record_path = skill_dir / AUDIT_RECORD_NAME
    record, read_concern = read_record(record_path)
    concerns: list[Concern] = []
    try:
        current_hashes = compute_audit_hashes(source.package_root, source.skills_root, skill_name)
    except DriftCheckError as exc:
        current_hashes = {}
        concerns.append(Concern("hash-unavailable", str(exc)))

    if read_concern is not None:
        concerns.append(read_concern)

    recorded_hashes: dict[str, Any] | None = None
    timestamp: str | None = None
    if record is not None:
        concerns.extend(validate_record_shape(record, skill_name))
        recorded_hashes = recorded_hashes_from_record(record)
        if recorded_hashes is not None:
            concerns.extend(compare_hashes(recorded_hashes, current_hashes))
        timestamp_raw = record.get("timestamp")
        if isinstance(timestamp_raw, str):
            timestamp = timestamp_raw

    return SkillDriftReport(
        skill=skill_name,
        derived_status="audit-current" if not concerns else "audit-stale",
        concerns=concerns,
        record_path=record_path,
        current_hashes=current_hashes,
        recorded_hashes=recorded_hashes,
        source=source.source,
        package_root=source.package_root,
        skills_root=source.skills_root,
        timestamp=timestamp,
        health_checks=(
            health_checks_for_skill(source, skill_name, validator_health=validator_health)
            if with_test_validate
            else None
        ),
    )


def build_payload(reports: list[SkillDriftReport]) -> dict[str, Any]:
    summary = {"audit-current": 0, "audit-stale": 0}
    for report in reports:
        summary[report.derived_status] += 1
    if any(report.health_checks is not None for report in reports):
        summary.update({"health-passed": 0, "health-failed": 0, "needs-attention": 0, "ok": 0})
        for report in reports:
            if report.health_status in {"health-passed", "health-failed"}:
                summary[report.health_status] += 1
            summary[report.overall_status] += 1
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "skills": [report.as_payload() for report in reports],
    }


def build_hash_payload(
    reports: list[SkillHashReport],
    failures: Sequence[SkillHashFailure] = (),
) -> dict[str, Any]:
    payload = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "computed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "skills": [report.as_payload() for report in reports],
    }
    if failures:
        payload["errors"] = [failure.as_payload() for failure in failures]
    return payload


def render_text(reports: list[SkillDriftReport]) -> str:
    payload = build_payload(reports)
    include_health = any(report.health_checks is not None for report in reports)
    lines = [
        "# Skill Drift Report",
        "",
        f"Observed skills: {len(reports)}",
        f"Audit current: {payload['summary']['audit-current']}",
        f"Audit stale: {payload['summary']['audit-stale']}",
    ]
    if include_health:
        lines.extend(
            [
                f"Health passed: {payload['summary']['health-passed']}",
                f"Health failed: {payload['summary']['health-failed']}",
                f"Needs attention: {payload['summary']['needs-attention']}",
                f"OK: {payload['summary']['ok']}",
            ]
        )
    lines.append("")
    if include_health:
        lines.extend(
            [
                "| Source | Skill | Audit status | Health status | Overall status | Record | Concerns |",
                "|---|---|---|---|---|---|---|",
            ]
        )
    else:
        lines.extend(
            [
                "| Source | Skill | Audit status | Record | Concerns |",
                "|---|---|---|---|---|",
            ]
        )
    for report in reports:
        cells = [
            markdown_cell(report.source),
            markdown_cell(report.skill),
            markdown_cell(report.derived_status),
        ]
        if include_health:
            cells.extend(
                [
                    markdown_cell(report.health_status),
                    markdown_cell(report.overall_status),
                ]
            )
        cells.extend(
            [
                markdown_cell(display_path(report.record_path, report.package_root)),
                markdown_cell(render_concerns_cell(report)),
            ]
        )
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def render_hash_text(
    reports: list[SkillHashReport],
    failures: Sequence[SkillHashFailure] = (),
) -> str:
    lines = [
        "# Skill Hash Report",
        "",
        f"Computed skills: {len(reports)}",
        "",
        "| Source | Skill | Root artifact hash | Policy hash | Node hashes |",
        "|---|---|---|---|---|",
    ]
    for report in reports:
        nodes = report.hashes.get("nodes")
        if isinstance(nodes, dict):
            root = nodes.get(report.skill, {})
            root_hash = root.get("artifact_graph_hash", "") if isinstance(root, dict) else ""
            node_text = "<br>".join(
                f"{node_id} [{values.get('blueprint_type', '')}]: "
                f"local_hash={values.get('local_hash', '')}; "
                f"artifact_graph_hash={values.get('artifact_graph_hash', '')}; "
                "expected_certified_health_hash="
                f"{values.get('expected_certified_health_hash', '')}"
                for node_id, values in sorted(nodes.items())
                if isinstance(values, dict)
            )
        else:
            interfaces = report.hashes.get("interfaces", {})
            root_hash = report.hashes.get("skill", "")
            node_text = "<br>".join(
                f"{name}: {value}"
                for name, value in sorted(interfaces.items())
                if isinstance(value, str)
            )
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(report.source),
                    markdown_cell(report.skill),
                    markdown_cell(str(root_hash)),
                    markdown_cell(str(report.hashes.get("policy", ""))),
                    markdown_cell(node_text or "none"),
                ]
            )
            + " |"
        )
    if failures:
        lines.extend(["", "Errors:"])
        for failure in failures:
            lines.append(
                f"- {failure.source}:{failure.skill}: hash-unavailable: {failure.message}"
            )
    return "\n".join(lines) + "\n"


def write_markdown_report(markdown: str, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now().astimezone()).strftime("%Y-%m-%d_%H-%M-%S")
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    path = BUILD_DIR / f"{timestamp}.md"
    path.write_text(markdown, encoding="utf-8")
    return path


def markdown_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def render_concerns_cell(report: SkillDriftReport) -> str:
    rendered: list[str] = []
    if report.concerns:
        for concern in report.concerns:
            detail = f" [{concern.key}]" if concern.key else ""
            rendered.append(f"{concern.kind}{detail}: {concern.message}")
    if report.health_checks is not None:
        for check in report.health_checks:
            if check.passed:
                continue
            rendered.append(f"health-check-failed [{check.name}]: returncode {check.returncode}")
    if not rendered:
        return "none"
    return "<br>".join(rendered)


def render_one_text(report: SkillDriftReport) -> str:
    lines = [
        f"Skill Drift: {report.skill}",
        "=" * (14 + len(report.skill)),
        "",
        f"Source: {report.source}",
        f"Audit status: {report.derived_status}",
        f"Record: {display_path(report.record_path, report.package_root)}",
    ]
    if report.timestamp:
        lines.extend(["", "Recorded state:"])
        lines.append(f"  timestamp: {report.timestamp}")
    lines.extend(["", "Concerns:"])
    if report.concerns:
        for concern in report.concerns:
            detail = f" [{concern.key}]" if concern.key else ""
            lines.append(f"  - {concern.kind}{detail}: {concern.message}")
    else:
        lines.append("  none")
    if report.health_checks is not None:
        lines.extend(["", "Health:"])
        lines.append(f"  status: {report.health_status}")
        lines.append(f"  overall: {report.overall_status}")
        for check in report.health_checks:
            detail = "skipped" if check.skipped else f"returncode {check.returncode}"
            lines.append(f"  - {check.name}: {'passed' if check.passed else 'failed'} ({detail})")
    lines.extend(["", "Hashes:"])
    for key, value in sorted(flatten_hashes(report.current_hashes).items()):
        recorded_value = flatten_hashes(report.recorded_hashes or {}).get(key)
        if recorded_value is None:
            lines.append(f"  {key}:")
            lines.append("    recorded: <missing>")
            lines.append(f"    current:  {value}")
        else:
            lines.append(f"  {key}:")
            lines.append(f"    recorded: {recorded_value}")
            lines.append(f"    current:  {value}")
    return "\n".join(lines) + "\n"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Famulus skill drift checker.")
    subparsers = parser.add_subparsers(dest="command")
    status = subparsers.add_parser("status", help="Check one or more skills, or all observed skills.")
    status.add_argument("skills", nargs="*")
    status.add_argument("--all", action="store_true", help="Check all observed skills.")
    status.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    status.add_argument(
        "--with-test-validate",
        action="store_true",
        help="Also run repo validators and each target skill's tests, then OR failures with audit staleness.",
    )
    status.add_argument("--repo-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    status.add_argument("--skill-root", type=Path, help="Check an exact installed skill root.")
    status.add_argument("--skills-root", type=Path, help=argparse.SUPPRESS)
    hashes = subparsers.add_parser("compute-hashes", help="Compute current hashes for blueprint-backed skills.")
    hashes.add_argument("skills", nargs="*")
    hashes.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    hashes.add_argument("--repo-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    hashes.add_argument("--skill-root", type=Path, help="Compute hashes for an exact installed skill root.")
    hashes.add_argument("--skills-root", type=Path, help=argparse.SUPPRESS)
    return parser


def run_status(args: argparse.Namespace) -> int:
    if args.skills and args.all:
        raise DriftCheckError("status accepts either skill names or --all, not both")

    scopes = requested_scopes(args)
    if not scopes:
        raise DriftCheckError("no installed skill roots were found")

    reports = reports_for_scopes(scopes, with_test_validate=args.with_test_validate)
    if args.json:
        print(json.dumps(build_payload(reports), indent=2, sort_keys=True))
    else:
        markdown = render_text(reports)
        report_path = write_markdown_report(markdown)
        print(markdown, end="")
        print(f"\nSaved report: {report_path.as_posix()}")
    return 0


def run_compute_hashes(args: argparse.Namespace) -> int:
    scopes = requested_scopes(args)
    if not scopes:
        raise DriftCheckError("no installed skill roots were found")

    reports, failures = hash_reports_for_scopes(scopes)
    if args.json:
        print(json.dumps(build_hash_payload(reports, failures), indent=2, sort_keys=True))
    else:
        print(render_hash_text(reports, failures), end="")
    for failure in failures:
        print(f"error: {failure.message}", file=sys.stderr)
    return 2 if failures else 0


def requested_skill_sources(args: argparse.Namespace) -> list[SkillSource]:
    if args.skills_root is not None:
        skills_root = args.skills_root.resolve()
        return [SkillSource(source="override", package_root=skills_root.parent, skills_root=skills_root)]
    if args.repo_root != REPO_ROOT:
        repo_root = args.repo_root.resolve()
        return [SkillSource(source="override", package_root=repo_root, skills_root=repo_root / "skills")]
    return observed_skill_sources()


def requested_scopes(args: argparse.Namespace) -> tuple[RequestedScope, ...]:
    if args.skill_root is not None:
        root = args.skill_root.expanduser().resolve()
        source = source_for_skill_root(root, source="override")
        return (RequestedScope(source, (root.name,)),)

    scopes: list[RequestedScope] = []
    named_requests: list[str] = []
    for requested in args.skills:
        if is_path_like_target(requested):
            root = Path(requested).expanduser().resolve()
            scopes.append(RequestedScope(source_for_skill_root(root), (root.name,)))
        else:
            named_requests.append(requested)

    for source in requested_skill_sources(args):
        if named_requests:
            names = tuple(named_requests)
        elif args.skills:
            continue
        elif args.command == "status":
            names = tuple(observed_skill_names(source.skills_root))
        else:
            names = tuple(blueprint_skill_names(source.skills_root))
        scopes.append(RequestedScope(source, names))
    return tuple(scopes)


def reports_for_scopes(
    scopes: tuple[RequestedScope, ...],
    *,
    with_test_validate: bool = False,
) -> list[SkillDriftReport]:
    reports: list[SkillDriftReport] = []
    requested_names = {
        skill_name
        for scope in scopes
        for skill_name in scope.skill_names
    }
    found_names: set[str] = set()
    validator_cache: dict[Path, HealthCheck] = {}

    def validator_health_for(source: SkillSource) -> HealthCheck | None:
        if not with_test_validate:
            return None
        key = source.package_root
        if key not in validator_cache:
            validator_cache[key] = run_validator_health(source)
        return validator_cache[key]

    for scope in scopes:
        for skill_name in scope.skill_names:
            if not (scope.source.skills_root / skill_name / "SKILL.md").is_file():
                continue
            found_names.add(skill_name)
            reports.append(
                check_skill(
                    scope.source,
                    skill_name,
                    with_test_validate=with_test_validate,
                    validator_health=validator_health_for(scope.source),
                )
            )
    missing = sorted(requested_names - found_names)
    if missing:
        raise DriftCheckError(f"skill(s) not found in installed skill roots: {', '.join(missing)}")
    return reports


def hash_reports_for_scopes(
    scopes: tuple[RequestedScope, ...],
) -> tuple[list[SkillHashReport], list[SkillHashFailure]]:
    reports: list[SkillHashReport] = []
    failures: list[SkillHashFailure] = []
    requested_names = {
        skill_name
        for scope in scopes
        for skill_name in scope.skill_names
    }
    found_names: set[str] = set()
    for scope in scopes:
        for skill_name in scope.skill_names:
            if not (scope.source.skills_root / skill_name / "SKILL.md").is_file():
                continue
            found_names.add(skill_name)
            try:
                reports.append(hash_report_for_skill(scope.source, skill_name))
            except DriftCheckError as exc:
                failures.append(
                    SkillHashFailure(
                        skill=skill_name,
                        source=scope.source.source,
                        package_root=scope.source.package_root,
                        skills_root=scope.source.skills_root,
                        message=str(exc),
                    )
                )
    missing = sorted(requested_names - found_names)
    if missing:
        raise DriftCheckError(f"skill(s) not found in installed skill roots: {', '.join(missing)}")
    return reports, failures


def hash_report_for_skill(source: SkillSource, skill_name: str) -> SkillHashReport:
    skill_dir = skill_dir_for(source.skills_root, skill_name)
    blueprint = load_blueprint(skill_dir)
    if blueprint.get("schema_version") == 2:
        return typed_hash_report_for_skill(source, skill_name)
    return SkillHashReport(
        skill=skill_name,
        source=source.source,
        package_root=source.package_root,
        skills_root=source.skills_root,
        hashes=compute_audit_hashes(source.package_root, source.skills_root, skill_name),
    )


def typed_hash_report_for_skill(source: SkillSource, skill_name: str) -> SkillHashReport:
    try:
        with secure_schema_snapshot(source.package_root) as schema_root:
            graph = load_validated_skill_blueprint_graph(
                skill_dir_for(source.skills_root, skill_name),
                schema_root,
            )
            policy_hash = compute_policy_hash(source.package_root)
            schema_hash = blueprint_schema_hash(schema_root)
            records: dict[str, dict[str, Any]] = {}
            for node_id, node in graph.nodes.items():
                record, concern = read_target_record(
                    source.package_root,
                    health_path_for_node(node),
                )
                if concern is None and record is not None:
                    records[node_id] = record

            key_path = (
                source.package_root
                / "skills"
                / "skill-audit"
                / ".health-authentication-key"
            )
            try:
                key = secure_load_target_key(source.package_root, key_path)
            except FileNotFoundError:
                key = b"\0" * HMAC_KEY_BYTES
                records = {}
            except ValueError:
                key = b"\0" * HMAC_KEY_BYTES
                records = {}
            report = check_graph_health(
                graph,
                records,
                policy_hash=policy_hash,
                schema_hash=schema_hash,
                key=key,
                schema_root=schema_root,
            )
            node_states = compute_node_hash_states(
                graph,
                policy_hash=policy_hash,
                schema_hash=schema_hash,
                checks_by_node={},
                schema_root=schema_root,
                certifier={
                    "interface": "skill-audit.machine.certify",
                    "version": 1,
                },
            )
            return SkillHashReport.from_graph_report(
                source,
                graph,
                report,
                policy_hash=policy_hash,
                schema_hash=schema_hash,
                node_states=node_states,
            )
    except DriftCheckError:
        raise
    except (
        OSError,
        TypeError,
        ValueError,
        KeyError,
        jsonschema.exceptions.SchemaError,
    ) as exc:
        raise DriftCheckError(
            f"{skill_name}: typed hash unavailable: {exc}"
        ) from exc


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command in {"status", "compute-hashes"}:
        try:
            if args.command == "status":
                return run_status(args)
            return run_compute_hashes(args)
        except DriftCheckError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
    parser.print_help(sys.stderr)
    return 2


class Interface(PythonArgvMachineInterface):
    """Dispatcher adapter for the drift status reporter."""

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
