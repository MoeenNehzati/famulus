#!/usr/bin/env python3
"""Skill drift status reporter.

The checker compares installed skills against their local audit records. JSON
mode writes only to stdout; the default human-readable mode also saves a
timestamped Markdown report under the skill's ignored ``_build`` directory.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import yaml

RTX_DIR = Path(__file__).resolve().parent
if str(RTX_DIR) not in sys.path:
    sys.path.insert(0, str(RTX_DIR))

from _drift_hashes import HashEntry, digest_entries, entries_for_path, hash_interface, hash_skill
from _skill_sources import SkillSource, observed_skill_sources
from officina.runtime.python_machine_interface import PythonArgvMachineInterface

REPO_ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = RTX_DIR.parent
BUILD_DIR = SKILL_ROOT / "_build"
AUDIT_RECORD_NAME = ".last_audit.json"
SCHEMA_VERSION = 1
POLICY_PATTERNS = (
    "references/skill-guidelines.md",
    "references/blueprint/schema.json",
    "references/blueprint/template.yaml",
    "references/blueprint/guide.md",
    "docs/plans/health-plan.md",
    "skills/skill-maker/validators/**/*.py",
    "validators/**/*.py",
    ".githooks/pre-commit",
    ".githooks/skill/**",
    "skills/skill-drift/_rtx/*.py",
    "skills/skill-drift/references/**/*.md",
)


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
    recorded_at: str | None = None
    writer: str | None = None

    def as_payload(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "source": self.source,
            "derived_status": self.derived_status,
            "concerns": [concern.as_payload() for concern in self.concerns],
            "record_path": display_path(self.record_path, self.package_root),
            "package_root": self.package_root.as_posix(),
            "skills_root": self.skills_root.as_posix(),
            "recorded_at": self.recorded_at,
            "writer": self.writer,
            "recorded_hashes": self.recorded_hashes,
            "current_hashes": self.current_hashes,
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


def skill_dir_for(skills_root: Path, skill_name: str) -> Path:
    skill_dir = skills_root / skill_name
    if not (skill_dir / "SKILL.md").is_file():
        raise DriftCheckError(f"skill `{skill_name}` does not exist or lacks SKILL.md")
    return skill_dir


def load_blueprint(skill_dir: Path) -> dict[str, Any]:
    path = skill_dir / "blueprint.yaml"
    if not path.is_file():
        raise DriftCheckError(f"{skill_dir.name}: missing blueprint.yaml")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise DriftCheckError(f"{path}: top level must be a mapping")
    return raw


def compute_policy_hash(repo_root: Path) -> str:
    entries: list[HashEntry] = []
    for pattern in POLICY_PATTERNS:
        if any(char in pattern for char in "*?[]"):
            paths = sorted(repo_root.glob(pattern), key=lambda item: item.as_posix())
        else:
            paths = [repo_root / pattern]
        for path in paths:
            if not path.exists() and not path.is_symlink():
                continue
            entries.extend(entries_for_path(path, repo_root))
    return digest_entries(entries)


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
    schema_version = record.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        if "schema_version" in record:
            concerns.append(
                Concern(
                    "unsupported-schema",
                    f"record schema_version {schema_version!r} is not supported",
                )
            )
        else:
            concerns.append(Concern("corrupt-record", "record is missing schema_version"))

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
    if "policy" in hashes and not isinstance(hashes["policy"], str):
        concerns.append(Concern("corrupt-record", "hashes.policy must be a string"))
    interfaces = hashes.get("interfaces")
    if "interfaces" in hashes and not (
        isinstance(interfaces, dict)
        and all(isinstance(key, str) and isinstance(value, str) for key, value in interfaces.items())
    ):
        concerns.append(Concern("corrupt-record", "hashes.interfaces must be an object of string hashes"))
    return concerns


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


def check_skill(source: SkillSource, skill_name: str) -> SkillDriftReport:
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
    recorded_at: str | None = None
    writer: str | None = None
    if record is not None:
        concerns.extend(validate_record_shape(record, skill_name))
        hashes = record.get("hashes")
        if isinstance(hashes, dict):
            recorded_hashes = hashes
            concerns.extend(compare_hashes(recorded_hashes, current_hashes))
        recorded_at_raw = record.get("recorded_at")
        if isinstance(recorded_at_raw, str):
            recorded_at = recorded_at_raw
        writer_raw = record.get("writer")
        if isinstance(writer_raw, str):
            writer = writer_raw

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
        recorded_at=recorded_at,
        writer=writer,
    )


def build_payload(reports: list[SkillDriftReport]) -> dict[str, Any]:
    summary = {"audit-current": 0, "audit-stale": 0}
    for report in reports:
        summary[report.derived_status] += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "summary": summary,
        "skills": [report.as_payload() for report in reports],
    }


def render_text(reports: list[SkillDriftReport]) -> str:
    payload = build_payload(reports)
    lines = [
        "# Skill Drift Report",
        "",
        f"Observed skills: {len(reports)}",
        f"Audit current: {payload['summary']['audit-current']}",
        f"Audit stale: {payload['summary']['audit-stale']}",
        "",
        "| Source | Skill | Audit status | Record | Concerns |",
        "|---|---|---|---|---|",
    ]
    for report in reports:
        lines.append(
            "| "
            + " | ".join(
                [
                    markdown_cell(report.source),
                    markdown_cell(report.skill),
                    markdown_cell(report.derived_status),
                    markdown_cell(display_path(report.record_path, report.package_root)),
                    markdown_cell(render_concerns_cell(report)),
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
    if not report.concerns:
        return "none"
    rendered: list[str] = []
    for concern in report.concerns:
        detail = f" [{concern.key}]" if concern.key else ""
        rendered.append(f"{concern.kind}{detail}: {concern.message}")
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
    if report.recorded_at or report.writer:
        lines.extend(["", "Recorded state:"])
        if report.recorded_at:
            lines.append(f"  recorded_at: {report.recorded_at}")
        if report.writer:
            lines.append(f"  writer: {report.writer}")
    lines.extend(["", "Concerns:"])
    if report.concerns:
        for concern in report.concerns:
            detail = f" [{concern.key}]" if concern.key else ""
            lines.append(f"  - {concern.kind}{detail}: {concern.message}")
    else:
        lines.append("  none")
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
    status.add_argument("--repo-root", type=Path, default=REPO_ROOT, help=argparse.SUPPRESS)
    status.add_argument("--skill-root", type=Path, help=argparse.SUPPRESS)
    status.add_argument("--skills-root", type=Path, help=argparse.SUPPRESS)
    return parser


def run_status(args: argparse.Namespace) -> int:
    if args.skills and args.all:
        raise DriftCheckError("status accepts either skill names or --all, not both")

    sources = requested_skill_sources(args)
    if not sources:
        raise DriftCheckError("no installed skill roots were found")

    reports = reports_for_sources(sources, list(args.skills))
    if args.json:
        print(json.dumps(build_payload(reports), indent=2, sort_keys=True))
    else:
        markdown = render_text(reports)
        report_path = write_markdown_report(markdown)
        print(markdown, end="")
        print(f"\nSaved report: {report_path.as_posix()}")
    return 0


def requested_skill_sources(args: argparse.Namespace) -> list[SkillSource]:
    if args.skill_root is not None:
        skill_root = args.skill_root.resolve()
        if skill_root.parent.name != "skills":
            raise DriftCheckError("--skill-root must point at a skill directory under a skills root")
        return [
            SkillSource(
                source="override",
                package_root=skill_root.parent.parent.resolve(),
                skills_root=skill_root.parent.resolve(),
            )
        ]
    if args.skills_root is not None:
        skills_root = args.skills_root.resolve()
        return [SkillSource(source="override", package_root=skills_root.parent, skills_root=skills_root)]
    if args.repo_root != REPO_ROOT:
        repo_root = args.repo_root.resolve()
        return [SkillSource(source="override", package_root=repo_root, skills_root=repo_root / "skills")]
    return observed_skill_sources()


def reports_for_sources(sources: list[SkillSource], requested_skills: list[str]) -> list[SkillDriftReport]:
    reports: list[SkillDriftReport] = []
    missing: list[str] = []
    if requested_skills:
        for skill_name in requested_skills:
            matches = [source for source in sources if (source.skills_root / skill_name / "SKILL.md").is_file()]
            if not matches:
                missing.append(skill_name)
                continue
            reports.extend(check_skill(source, skill_name) for source in matches)
    else:
        for source in sources:
            reports.extend(check_skill(source, skill_name) for skill_name in observed_skill_names(source.skills_root))
    if missing:
        raise DriftCheckError(f"skill(s) not found in installed skill roots: {', '.join(missing)}")
    return reports


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    if args.command == "status":
        try:
            return run_status(args)
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
