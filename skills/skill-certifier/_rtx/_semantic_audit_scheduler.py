#!/usr/bin/env python3
"""Locked bounded scheduler for semantic certification audits."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence

from officina.certification.dependency_dag import (
    DependencyDagError,
    decode_dependency_dag,
)
from officina.common.atomic_files import (
    atomic_create_bytes,
    atomic_replace_bytes,
    exclusive_file_lock,
)
from officina.runtime.python_machine_interface import PythonArgvMachineInterface


RTX_DIR = Path(__file__).resolve().parent
RUN_ROOT = RTX_DIR.parent / "_build" / "semantic-audit-runs"
STATE_SCHEMA = "skill-certifier.semantic-audit-state/v1"
RESULT_SCHEMA = "skill-certifier.semantic-audit-result/v1"


class SchedulerError(RuntimeError):
    """Raised when a scheduler transition is invalid."""


def _canonical(payload: object) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def _prefix_paths(prefix: Path) -> tuple[Path, Path, Path, Path]:
    root = RUN_ROOT.resolve()
    root.mkdir(parents=True, exist_ok=True)
    selected = Path(prefix).expanduser().resolve()
    if selected == root or not selected.is_relative_to(root):
        raise SchedulerError(f"prefix must be beneath {root}")
    return (
        selected.parent / f"{selected.name}.dag.json",
        selected.parent / f"{selected.name}.state.json",
        selected.parent / f"{selected.name}.lock",
        selected.parent / f"{selected.name}.inputs",
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SchedulerError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise SchedulerError(f"JSON object required: {path}")
    return payload


def _write_state(path: Path, payload: dict[str, Any]) -> None:
    atomic_replace_bytes(path, _canonical(payload), allowed_root=RUN_ROOT, mode=0o600)


def _dag_index(dag: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {node["id"]: node for node in dag["nodes"]}


def _evidence_paths(drift: dict[str, Any]) -> dict[str, str]:
    result: dict[str, str] = {}
    skills = drift.get("skills", [])
    if not isinstance(skills, list):
        return result
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        for field in ("nodes", "dependency_nodes"):
            entries = skill.get(field, [])
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                node_id = entry.get("node_id")
                certificate_path = entry.get("certificate_path")
                if isinstance(node_id, str) and isinstance(certificate_path, str):
                    result[node_id] = certificate_path
    return result


def _summary(state: dict[str, Any], *, selected: list[dict[str, str]] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "status": state["status"],
        "required_count": len(state["required"]),
        "audited_count": len(state["audited"]),
        "in_progress_count": len(state["in_progress"]),
        "reason": state["reason"],
    }
    if selected is not None:
        result["selected"] = selected
    return result


def initialize(prefix: Path, dag_file: Path, drift_file: Path) -> dict[str, Any]:
    dag_path, state_path, lock_path, inputs_dir = _prefix_paths(prefix)
    try:
        dag = decode_dependency_dag(_read_json(Path(dag_file)))
    except DependencyDagError as exc:
        raise SchedulerError(str(exc)) from exc
    drift = _read_json(Path(drift_file))
    required = drift.get("stale_vertices")
    if not isinstance(required, list) or not all(isinstance(item, str) for item in required):
        raise SchedulerError("drift stale_vertices must be a string array")
    if required != sorted(set(required)):
        raise SchedulerError("stale_vertices must be sorted and unique")
    nodes = _dag_index(dag)
    unknown = sorted(set(required) - set(nodes))
    if unknown:
        raise SchedulerError("unknown stale vertices: " + ", ".join(unknown))
    proposed = {
        "schema_version": STATE_SCHEMA,
        "repository": dag["repository"],
        "dag_digest": "sha256:" + hashlib.sha256(_canonical(dag)).hexdigest(),
        "required": required,
        "audited": [],
        "in_progress": [],
        "reports": {},
        "evidence_paths": _evidence_paths(drift),
        "status": "complete" if not required else "active",
        "reason": None,
    }
    with exclusive_file_lock(lock_path, allowed_root=RUN_ROOT):
        if state_path.exists():
            existing = _read_json(state_path)
            identity_fields = ("repository", "dag_digest", "required", "evidence_paths")
            if any(existing.get(field) != proposed[field] for field in identity_fields):
                raise SchedulerError("conflicting scheduler prefix reuse")
            return _summary(existing)
        inputs_dir.mkdir(mode=0o700)
        atomic_create_bytes(dag_path, _canonical(dag), allowed_root=RUN_ROOT, mode=0o600)
        atomic_create_bytes(state_path, _canonical(proposed), allowed_root=RUN_ROOT, mode=0o600)
        return _summary(proposed)


def claim(prefix: Path, capacity: int) -> dict[str, Any]:
    if isinstance(capacity, bool) or not 0 <= capacity <= 64:
        raise SchedulerError("capacity must be between 0 and 64")
    dag_path, state_path, lock_path, inputs_dir = _prefix_paths(prefix)
    with exclusive_file_lock(lock_path, allowed_root=RUN_ROOT):
        state = _read_json(state_path)
        if state["status"] != "active":
            return _summary(state, selected=[])
        dag = decode_dependency_dag(_read_json(dag_path))
        nodes = _dag_index(dag)
        required = set(state["required"])
        audited = set(state["audited"])
        in_progress = set(state["in_progress"])
        slots = max(capacity - len(in_progress), 0)
        ready = [
            task_id
            for task_id in state["required"]
            if task_id not in audited
            and task_id not in in_progress
            and all(
                dependency not in required or dependency in audited
                for dependency in nodes[task_id]["dependencies"]
            )
        ]
        selected_items: list[dict[str, str]] = []
        for task_id in ready[:slots]:
            dependency_reports = [
                state["reports"][dependency]
                for dependency in nodes[task_id]["dependencies"]
                if dependency in required
            ]
            reusable = []
            for dependency in nodes[task_id]["dependencies"]:
                if dependency in required:
                    continue
                dependency_node = nodes[dependency]
                owner = dependency_node["owner_node_id"] or dependency
                reusable.append(
                    {
                        "task_id": dependency,
                        "certificate_path": state["evidence_paths"].get(owner),
                    }
                )
            packet = {
                "repository": state["repository"],
                "task_id": task_id,
                "kind": nodes[task_id]["kind"],
                "owner_node_id": nodes[task_id]["owner_node_id"],
                "dependency_reports": dependency_reports,
                "reusable_dependencies": reusable,
            }
            packet_path = inputs_dir / (hashlib.sha256(task_id.encode()).hexdigest() + ".json")
            atomic_replace_bytes(packet_path, _canonical(packet), allowed_root=RUN_ROOT, mode=0o600)
            selected_items.append(
                {
                    "task_id": task_id,
                    "kind": nodes[task_id]["kind"],
                    "input_file": packet_path.as_posix(),
                }
            )
            in_progress.add(task_id)
        state["in_progress"] = sorted(in_progress)
        _write_state(state_path, state)
        return _summary(state, selected=selected_items)


def _validate_report(report: dict[str, Any], task_id: str) -> None:
    required_fields = {
        "schema_version", "task_id", "verdict", "summary", "evidence",
        "consumed_dependencies", "findings",
    }
    if set(report) != required_fields or report.get("schema_version") != RESULT_SCHEMA:
        raise SchedulerError("invalid semantic audit report")
    if report.get("task_id") != task_id:
        raise SchedulerError("semantic audit task mismatch")
    verdict = report.get("verdict")
    if verdict not in {"pass", "reject", "abort"}:
        raise SchedulerError("invalid semantic audit verdict")
    if not isinstance(report.get("summary"), str) or not report["summary"]:
        raise SchedulerError("semantic audit summary is required")
    collections = [report.get(field) for field in ("evidence", "findings")]
    if not all(isinstance(items, list) for items in collections):
        raise SchedulerError("semantic audit evidence/findings must be arrays")
    if not all(isinstance(item, str) for items in collections for item in items):
        raise SchedulerError("semantic audit evidence/findings must be strings")
    consumed = report.get("consumed_dependencies")
    if not isinstance(consumed, list) or any(
        not isinstance(item, dict)
        or set(item) != {"task_id", "verdict"}
        or not isinstance(item["task_id"], str)
        or item["verdict"] != "pass"
        for item in consumed
    ):
        raise SchedulerError("invalid consumed dependencies")
    findings = report["findings"]
    if (verdict == "pass" and findings) or (verdict != "pass" and not findings):
        raise SchedulerError("findings do not match verdict")


def complete(prefix: Path, task_id: str, report_file: Path) -> dict[str, Any]:
    dag_path, state_path, lock_path, _inputs_dir = _prefix_paths(prefix)
    with exclusive_file_lock(lock_path, allowed_root=RUN_ROOT):
        state = _read_json(state_path)
        try:
            if state["status"] != "active" or task_id not in state["in_progress"]:
                raise SchedulerError(f"task is not in progress: {task_id}")
            report = _read_json(Path(report_file))
            _validate_report(report, task_id)
            dag = decode_dependency_dag(_read_json(dag_path))
            nodes = _dag_index(dag)
            required = set(state["required"])
            expected = sorted(
                dependency
                for dependency in nodes[task_id]["dependencies"]
                if dependency in required
            )
            consumed = sorted(
                item["task_id"] for item in report["consumed_dependencies"]
            )
            if consumed != expected:
                raise SchedulerError("consumed dependencies do not match task input")
            if report["verdict"] != "pass":
                state["status"] = "failed"
                state["reason"] = report["summary"]
                state["in_progress"] = []
            else:
                state["in_progress"].remove(task_id)
                state["audited"] = sorted({*state["audited"], task_id})
                state["reports"][task_id] = report
                if state["audited"] == state["required"]:
                    state["status"] = "complete"
        except (DependencyDagError, SchedulerError) as exc:
            state["status"] = "failed"
            state["reason"] = str(exc)
            state["in_progress"] = []
        _write_state(state_path, state)
        return _summary(state)


def fail(prefix: Path, task_id: str, reason: str) -> dict[str, Any]:
    if not reason:
        raise SchedulerError("failure reason is required")
    _dag_path, state_path, lock_path, _inputs_dir = _prefix_paths(prefix)
    with exclusive_file_lock(lock_path, allowed_root=RUN_ROOT):
        state = _read_json(state_path)
        if state["status"] != "active" or task_id not in state["in_progress"]:
            raise SchedulerError(f"task is not in progress: {task_id}")
        state["status"] = "failed"
        state["reason"] = f"{task_id}: {reason}"
        state["in_progress"] = []
        _write_state(state_path, state)
        return _summary(state)


def abort(prefix: Path, reason: str) -> dict[str, Any]:
    if not reason:
        raise SchedulerError("abort reason is required")
    _dag_path, state_path, lock_path, _inputs_dir = _prefix_paths(prefix)
    with exclusive_file_lock(lock_path, allowed_root=RUN_ROOT):
        state = _read_json(state_path)
        if state["status"] != "active":
            raise SchedulerError("scheduler run is not active")
        state["status"] = "failed"
        state["reason"] = f"operator: {reason}"
        state["in_progress"] = []
        _write_state(state_path, state)
        return _summary(state)


def status(prefix: Path) -> dict[str, Any]:
    _dag_path, state_path, lock_path, _inputs_dir = _prefix_paths(prefix)
    with exclusive_file_lock(lock_path, allowed_root=RUN_ROOT):
        state = _read_json(state_path)
        result = _summary(state)
        result["in_progress"] = list(state["in_progress"])
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="operation", required=True)
    initialize_parser = commands.add_parser("initialize")
    initialize_parser.add_argument("prefix", type=Path)
    initialize_parser.add_argument("--dag-file", type=Path, required=True)
    initialize_parser.add_argument("--drift-file", type=Path, required=True)
    claim_parser = commands.add_parser("claim")
    claim_parser.add_argument("prefix", type=Path)
    claim_parser.add_argument("--capacity", type=int, required=True)
    complete_parser = commands.add_parser("complete")
    complete_parser.add_argument("prefix", type=Path)
    complete_parser.add_argument("task_id")
    complete_parser.add_argument("--report-file", type=Path, required=True)
    fail_parser = commands.add_parser("fail")
    fail_parser.add_argument("prefix", type=Path)
    fail_parser.add_argument("task_id")
    fail_parser.add_argument("--reason", required=True)
    status_parser = commands.add_parser("status")
    status_parser.add_argument("prefix", type=Path)
    abort_parser = commands.add_parser("abort")
    abort_parser.add_argument("prefix", type=Path)
    abort_parser.add_argument("--reason", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        if args.operation == "initialize":
            result = initialize(args.prefix, args.dag_file, args.drift_file)
        elif args.operation == "claim":
            result = claim(args.prefix, args.capacity)
        elif args.operation == "complete":
            result = complete(args.prefix, args.task_id, args.report_file)
        elif args.operation == "fail":
            result = fail(args.prefix, args.task_id, args.reason)
        elif args.operation == "status":
            result = status(args.prefix)
        else:
            result = abort(args.prefix, args.reason)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (OSError, ValueError, SchedulerError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


class Interface(PythonArgvMachineInterface):
    """Dispatcher adapter for semantic audit scheduling."""

    def run(self, argv: list[str]) -> int:
        return main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
