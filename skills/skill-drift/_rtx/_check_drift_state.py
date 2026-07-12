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
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from _drift_hashes import HashEntry, digest_entries, entries_for_path, hash_interface, hash_skill
from _skill_sources import SkillSource, observed_skill_sources
from officina.common.audit_records import RECORD_DIGEST_FIELD, record_digest_matches
from officina.blueprint_search import BlueprintSearchError, load_blueprint_record
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = RTX_DIR.parent
BUILD_DIR = SKILL_ROOT / "_build"
AUDIT_RECORD_NAME = ".last_audit.json"
OUTPUT_SCHEMA_VERSION = 1
POLICY_ROOTS_PATH = SKILL_ROOT / "references" / "policy-hash-roots.json"


class DriftCheckError(RuntimeError):
    """Raised when a requested skill cannot be checked."""


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

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "hashes": self.hashes,
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
    entries: list[HashEntry] = list(entries_for_path(POLICY_ROOTS_PATH, REPO_ROOT))
    for pattern in load_policy_patterns():
        if any(char in pattern for char in "*?[]"):
            paths = sorted(repo_root.glob(pattern), key=lambda item: item.as_posix())
        else:
            paths = [repo_root / pattern]
        for path in paths:
            if not path.exists() and not path.is_symlink():
                continue
            entries.extend(entries_for_path(path, repo_root))
    return digest_entries(entries)


def load_policy_patterns() -> list[str]:
    raw = json.loads(POLICY_ROOTS_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise DriftCheckError(f"{POLICY_ROOTS_PATH.as_posix()} must contain a JSON string list")
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


def check_skill(
    source: SkillSource,
    skill_name: str,
    *,
    with_test_validate: bool = False,
    validator_health: HealthCheck | None = None,
) -> SkillDriftReport:
    skill_dir = skill_dir_for(source.skills_root, skill_name)
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


def build_hash_payload(reports: list[SkillHashReport]) -> dict[str, Any]:
    return {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "computed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "skills": [report.as_payload() for report in reports],
    }


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


def render_hash_text(reports: list[SkillHashReport]) -> str:
    lines = [
        "# Skill Hash Report",
        "",
        f"Computed skills: {len(reports)}",
        "",
        "| Source | Skill | Skill hash | Policy hash | Interface hashes |",
        "|---|---|---|---|---|",
    ]
    for report in reports:
        interfaces = report.hashes.get("interfaces", {})
        interface_text = "<br>".join(
            f"{name}: {value}" for name, value in sorted(interfaces.items()) if isinstance(value, str)
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(report.source),
                    markdown_cell(report.skill),
                    markdown_cell(str(report.hashes.get("skill", ""))),
                    markdown_cell(str(report.hashes.get("policy", ""))),
                    markdown_cell(interface_text or "none"),
                ]
            )
            + " |"
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

    sources = requested_skill_sources(args)
    if not sources:
        raise DriftCheckError("no installed skill roots were found")

    reports = reports_for_sources(sources, list(args.skills), with_test_validate=args.with_test_validate)
    if args.json:
        print(json.dumps(build_payload(reports), indent=2, sort_keys=True))
    else:
        markdown = render_text(reports)
        report_path = write_markdown_report(markdown)
        print(markdown, end="")
        print(f"\nSaved report: {report_path.as_posix()}")
    return 0


def run_compute_hashes(args: argparse.Namespace) -> int:
    sources = requested_skill_sources(args)
    if not sources:
        raise DriftCheckError("no installed skill roots were found")

    reports = hash_reports_for_sources(sources, list(args.skills))
    if args.json:
        print(json.dumps(build_hash_payload(reports), indent=2, sort_keys=True))
    else:
        print(render_hash_text(reports), end="")
    return 0


def requested_skill_sources(args: argparse.Namespace) -> list[SkillSource]:
    if args.skill_root is not None:
        return [source_for_skill_root(args.skill_root, source="override")]
    if args.skills_root is not None:
        skills_root = args.skills_root.resolve()
        return [SkillSource(source="override", package_root=skills_root.parent, skills_root=skills_root)]
    if args.repo_root != REPO_ROOT:
        repo_root = args.repo_root.resolve()
        return [SkillSource(source="override", package_root=repo_root, skills_root=repo_root / "skills")]
    return observed_skill_sources()


def reports_for_sources(
    sources: list[SkillSource],
    requested_skills: list[str],
    *,
    with_test_validate: bool = False,
) -> list[SkillDriftReport]:
    reports: list[SkillDriftReport] = []
    missing: list[str] = []
    validator_cache: dict[Path, HealthCheck] = {}

    def validator_health_for(source: SkillSource) -> HealthCheck | None:
        if not with_test_validate:
            return None
        key = source.package_root
        if key not in validator_cache:
            validator_cache[key] = run_validator_health(source)
        return validator_cache[key]

    if requested_skills:
        for requested in requested_skills:
            if is_path_like_target(requested):
                skill_root = Path(requested).expanduser().resolve()
                source = source_for_skill_root(skill_root)
                reports.append(
                    check_skill(
                        source,
                        skill_root.name,
                        with_test_validate=with_test_validate,
                        validator_health=validator_health_for(source),
                    )
                )
                continue
            skill_name = requested
            matches = [source for source in sources if (source.skills_root / skill_name / "SKILL.md").is_file()]
            if not matches:
                missing.append(skill_name)
                continue
            reports.extend(
                check_skill(
                    source,
                    skill_name,
                    with_test_validate=with_test_validate,
                    validator_health=validator_health_for(source),
                )
                for source in matches
            )
    else:
        for source in sources:
            reports.extend(
                check_skill(
                    source,
                    skill_name,
                    with_test_validate=with_test_validate,
                    validator_health=validator_health_for(source),
                )
                for skill_name in observed_skill_names(source.skills_root)
            )
    if missing:
        raise DriftCheckError(f"skill(s) not found in installed skill roots: {', '.join(missing)}")
    return reports


def hash_reports_for_sources(sources: list[SkillSource], requested_skills: list[str]) -> list[SkillHashReport]:
    reports: list[SkillHashReport] = []
    missing: list[str] = []
    if requested_skills:
        for requested in requested_skills:
            if is_path_like_target(requested):
                skill_root = Path(requested).expanduser().resolve()
                source = source_for_skill_root(skill_root)
                reports.append(hash_report_for_skill(source, skill_root.name))
                continue
            skill_name = requested
            matches = [source for source in sources if (source.skills_root / skill_name / "SKILL.md").is_file()]
            if not matches:
                missing.append(skill_name)
                continue
            for source in matches:
                reports.append(hash_report_for_skill(source, skill_name))
    else:
        for source in sources:
            for skill_name in blueprint_skill_names(source.skills_root):
                reports.append(hash_report_for_skill(source, skill_name))
    if missing:
        raise DriftCheckError(f"skill(s) not found in installed skill roots: {', '.join(missing)}")
    return reports


def hash_report_for_skill(source: SkillSource, skill_name: str) -> SkillHashReport:
    return SkillHashReport(
        skill=skill_name,
        source=source.source,
        package_root=source.package_root,
        skills_root=source.skills_root,
        hashes=compute_audit_hashes(source.package_root, source.skills_root, skill_name),
    )


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
