#!/usr/bin/env python3
"""Tests for durable, privacy-bounded mathematical graph run diagnostics."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys

import jsonschema
import pytest
import yaml


SKILL_DIR = Path(__file__).resolve().parents[2]
REPO_SRC = SKILL_DIR.parents[1] / "src"
sys.path.insert(0, str(REPO_SRC))
sys.path.insert(0, str(SKILL_DIR / "_rtx"))

import _run_diagnostics as diagnostics  # noqa: E402
from _run_diagnostics import RunDiagnostics  # noqa: E402
from officina.blueprints.graph import BlueprintNode, InterfaceExport  # noqa: E402
from officina.blueprints.process_binding import (  # noqa: E402
    compile_gateway_invocation,
    parse_caller_invocation,
)


class FakeClock:
    """Advance UTC correlation time and monotonic duration time together."""

    def __init__(self, *, utc: str, monotonic_ns: int) -> None:
        self._utc = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        self._monotonic_ns = monotonic_ns

    def utc_now(self) -> datetime:
        """Return the controlled timezone-aware UTC instant."""

        return self._utc

    def monotonic_ns(self) -> int:
        """Return the controlled monotonic instant."""

        return self._monotonic_ns

    def advance(self, *, milliseconds: int) -> None:
        """Advance both clock domains by the requested whole milliseconds."""

        self._utc += timedelta(milliseconds=milliseconds)
        self._monotonic_ns += milliseconds * 1_000_000


def _entrypoint(tmp_path: Path) -> Path:
    """Create one small TeX entrypoint used only for byte/hash measurements."""

    path = tmp_path / "main.tex"
    path.write_text("\\documentclass{article}\n", encoding="utf-8")
    return path


def _clock() -> FakeClock:
    """Return a fresh deterministic test clock."""

    return FakeClock(utc="2026-08-19T12:00:00Z", monotonic_ns=1_000_000_000)


def _minimal_iterator_summary() -> dict:
    """Return prose-free iterator aggregates accepted by durable diagnostics."""

    timing = {"samples": 0, "total": 0, "maximum": 0}
    return {
        "setup": {
            "unit_count": 1,
            "worker_count": 1,
            "assigned_characters": 12,
            "internal_timings_ms": {
                "scan": 0,
                "unitization": 0,
                "partition": 0,
                "database": 0,
                "validation": 0,
                "total": 0,
            },
        },
        "next": {
            "calls": 1,
            "acknowledgements": 1,
            "wraps": 1,
            "retries": 0,
            "failures": 0,
            "open_sequence": {
                "count": 0,
                "unit_count": 0,
                "character_count": 0,
                "maximum_elapsed_ms": 0,
            },
            "internal_timings_ms": {
                name: dict(timing)
                for name in (
                    "validation",
                    "transaction",
                    "lookup",
                    "serialization",
                    "total",
                )
            },
        },
    }


def _diagnostics_process_interface() -> tuple[BlueprintNode, InterfaceExport]:
    """Load the authored diagnostics process surface for invocation compilation."""

    blueprint_path = SKILL_DIR / "_rtx" / "blueprints" / "rtx-run-diagnostics.yaml"
    blueprint = yaml.safe_load(blueprint_path.read_text(encoding="utf-8"))
    source = BlueprintNode(
        node_id=blueprint["id"],
        node_type=blueprint["node_type"],
        version=blueprint["version"],
        module_root=SKILL_DIR / "_rtx",
        blueprint_path=blueprint_path,
        gateway_path=SKILL_DIR / "_rtx" / blueprint["gateway"]["path"],
        declaration=blueprint,
    )
    interface_id = (
        f"{blueprint['id']}.interface.scripts-record-run-diagnostics"
    )
    declaration = blueprint["interfaces"][interface_id]
    return source, InterfaceExport(
        interface_id=interface_id,
        version=declaration["version"],
        local_name="scripts-record-run-diagnostics",
        module_node_id="math-dependency-graph._rtx",
        declaration=declaration,
        source_node_id=source.node_id,
        source_interface_id=interface_id,
    )


def test_diagnostics_interface_compiles_iterator_controller_timing_operation() -> None:
    """Dropping timing flags from the public process contract makes calls unusable."""

    source, interface = _diagnostics_process_interface()
    invocation = compile_gateway_invocation(
        source,
        interface,
        parse_caller_invocation(
            interface,
            [
                "iterator-controller-timing",
                "run-dir",
                "setup",
                "--process-dispatch-ms",
                "6",
                "--publication-ms",
                "2",
                "--total-ms",
                "23",
            ],
            stdin_requested=False,
        ),
    )

    assert invocation.argv == (
        "iterator-controller-timing",
        "run-dir",
        "setup",
        "--process-dispatch-ms",
        "6",
        "--publication-ms",
        "2",
        "--total-ms",
        "23",
    )


def test_controller_timing_process_operation_records_each_iterator_call(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Removing the process-facing timing operation leaves gateway calls unmeasured."""

    report = RunDiagnostics.initialize(tmp_path, entrypoint=_entrypoint(tmp_path))
    report.record_iterator_summary(_minimal_iterator_summary())

    diagnostics.main(
        [
            "iterator-controller-timing",
            str(tmp_path),
            "setup",
            "--process-dispatch-ms",
            "6",
            "--publication-ms",
            "2",
            "--total-ms",
            "23",
        ]
    )
    diagnostics.main(
        [
            "iterator-controller-timing",
            str(tmp_path),
            "next",
            "--process-dispatch-ms",
            "7",
            "--total-ms",
            "12",
        ]
    )
    capsys.readouterr()

    persisted = json.loads(report.path.read_text(encoding="utf-8"))
    assert persisted["iterator"]["setup"]["controller_timings_ms"] == {
        "process_dispatch": {"samples": 1, "total": 6, "maximum": 6},
        "publication": {"samples": 1, "total": 2, "maximum": 2},
        "total": {"samples": 1, "total": 23, "maximum": 23},
    }
    assert persisted["iterator"]["next"]["controller_timings_ms"] == {
        "process_dispatch": {"samples": 1, "total": 7, "maximum": 7},
        "total": {"samples": 1, "total": 12, "maximum": 12},
    }


def test_worker_lifecycle_records_queue_and_worker_durations(tmp_path: Path) -> None:
    """Swapping monotonic timing for wall-clock timing must break exact durations."""

    clock = _clock()
    output = tmp_path / "inventory-001.json"
    output.write_text('{"candidates": []}\n', encoding="utf-8")
    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=clock
    )

    report.worker_queued("inventory-001", phase="inventory", model="model-x")
    clock.advance(milliseconds=250)
    report.worker_started("inventory-001")
    clock.advance(milliseconds=750)
    report.worker_finished("inventory-001", status="success", output=output)

    job = report.payload["jobs"][0]
    assert job["queue_ms"] == 250
    assert job["worker_ms"] == 750
    assert job["status"] == "success"
    assert job["output_artifact"].startswith("inventory-fragment-")


def test_retry_and_failure_history_survives_later_success(tmp_path: Path) -> None:
    """Replacing a failed attempt instead of appending its retry loses evidence."""

    clock = _clock()
    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=clock
    )
    report.worker_queued("extract-001", phase="extract", model="model-y")
    report.worker_started("extract-001")
    clock.advance(milliseconds=30)
    report.worker_finished(
        "extract-001",
        status="failure",
        error=ValueError("WORKER_EXCEPTION_SENTINEL candidate partition omitted"),
        error_code="validation-failed",
    )
    report.worker_queued(
        "extract-001",
        phase="extract",
        model="model-y",
        retry_code="validation-failed",
    )
    report.worker_started("extract-001")
    output = tmp_path / "extract-001.json"
    output.write_text('{"entities": [], "relationships": []}\n', encoding="utf-8")
    clock.advance(milliseconds=20)
    report.worker_finished("extract-001", status="success", output=output)
    report.finish(status="success")

    reopened = RunDiagnostics.open(tmp_path, clock=clock)
    assert [job["status"] for job in reopened.payload["jobs"]] == [
        "failure",
        "success",
    ]
    assert reopened.payload["jobs"][0]["diagnostic"] == {
        "code": "validation-failed",
        "category": "validation",
        "exception_type": "ValueError",
        "message_sha256": hashlib.sha256(
            b"WORKER_EXCEPTION_SENTINEL candidate partition omitted"
        ).hexdigest(),
    }
    assert reopened.payload["jobs"][1]["retry"] == 1
    assert reopened.payload["jobs"][1]["retry_code"] == "validation-failed"
    assert reopened.payload["counts"]["jobs"] == 2
    assert reopened.payload["counts"]["retries"] == 1
    assert reopened.payload["counts"]["validation_errors"] == 1
    assert reopened.payload["run"]["status"] == "success"


def test_stage_context_records_success_and_reraises_failure(tmp_path: Path) -> None:
    """Suppressing a stage exception or using nondeterministic timing is a bug."""

    clock = _clock()
    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=clock
    )
    with report.stage(
        "planning",
        inputs=[tmp_path / "source-packet.txt"],
        outputs=[tmp_path / "inventory-chunks.json"],
    ):
        clock.advance(milliseconds=125)

    failure = RuntimeError("renderer failed\nwithout source details")
    with pytest.raises(RuntimeError) as caught:
        with report.stage("rendering"):
            clock.advance(milliseconds=75)
            raise failure

    assert caught.value is failure
    assert report.payload["stages"] == [
        {
            "operation": "planning",
            "category": "operation",
            "status": "success",
            "started_at_utc": "2026-08-19T12:00:00Z",
            "finished_at_utc": "2026-08-19T12:00:00.125000Z",
            "duration_ms": 125,
            "input_paths": [str(tmp_path / "source-packet.txt")],
            "output_paths": [str(tmp_path / "inventory-chunks.json")],
        },
        {
            "operation": "rendering",
            "category": "operation",
            "status": "failure",
            "started_at_utc": "2026-08-19T12:00:00.125000Z",
            "finished_at_utc": "2026-08-19T12:00:00.200000Z",
            "duration_ms": 75,
            "diagnostic": {
                "code": "stage-failed",
                "category": "stage",
                "exception_type": "RuntimeError",
                "message_sha256": hashlib.sha256(
                    b"renderer failed\nwithout source details"
                ).hexdigest(),
            },
        },
    ]


def test_artifacts_counts_and_all_ratios_are_recorded(tmp_path: Path) -> None:
    """A size-only report cannot diagnose semantic or compression regressions."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    paths_and_sizes = [
        ("inventory-packet", 100),
        ("inventory-fragment", 25),
        ("active-source", 200),
        ("pooled-inventory", 50),
        ("semantic-ir", 20),
        ("renderer-json", 10),
    ]
    artifacts = {}
    for kind, size in paths_and_sizes:
        path = tmp_path / f"{kind}.bin"
        path.write_bytes(kind.encode("ascii")[:1] * size)
        artifacts[kind] = report.record_artifact(
            path,
            kind=kind,
            phase="pooling" if "inventory" in kind else "compilation",
            counts={"candidates": 3} if kind == "pooled-inventory" else None,
        )

    report.record_ratio(
        "inventory-fragment-to-owned-packet",
        numerator=artifacts["inventory-fragment"],
        denominator=artifacts["inventory-packet"],
        job_id="inventory-001",
    )
    report.record_ratio(
        "pooled-inventory-to-active-source",
        numerator=artifacts["pooled-inventory"],
        denominator=artifacts["active-source"],
    )
    report.record_ratio(
        "pooled-canonical-fragments-to-owned-packets",
        numerator=artifacts["pooled-inventory"],
        denominator=artifacts["active-source"],
        numerator_bytes=35,
        denominator_bytes=100,
        measurement_basis="canonical-fragments-to-owned-lines",
    )
    report.record_ratio(
        "semantic-ir-to-pooled-inventory",
        numerator=artifacts["semantic-ir"],
        denominator=artifacts["pooled-inventory"],
    )
    report.record_ratio(
        "renderer-json-to-semantic-ir",
        numerator=artifacts["renderer-json"],
        denominator=artifacts["semantic-ir"],
    )
    report.record_semantic_counts(
        {
            "entities": [{}, {}],
            "relationships": [{}],
            "exclusions": [{}, {}, {}],
            "unresolved_resolutions": [{"disposition": "unresolved"}],
            "gaps": [{}, {}],
        }
    )

    assert [ratio["value"] for ratio in report.payload["ratios"]] == [
        0.25,
        0.25,
        0.35,
        0.4,
        0.5,
    ]
    renderer = artifacts["renderer-json"]
    assert renderer["bytes"] == 10
    assert renderer["sha256"] == hashlib.sha256(b"r" * 10).hexdigest()
    assert artifacts["pooled-inventory"]["counts"] == {"candidates": 3}
    assert report.payload["counts"] == {
        "jobs": 0,
        "retries": 0,
            "validation_errors": 0,
            "corrections": 0,
        "entities": 2,
        "relationships": 1,
        "exclusions": 3,
            "unresolved": 1,
            "unresolved_resolutions": 1,
        "gaps": 2,
    }


def test_every_persisted_payload_is_schema_valid_and_contains_no_artifact_prose(
    tmp_path: Path,
) -> None:
    """Artifact bytes may influence metrics but must never be copied into diagnostics."""

    sentinel = "PRIVATE_TEX_SOURCE PROMPT_SECRET IR_PROSE_SECRET"
    entrypoint = _entrypoint(tmp_path)
    artifact_path = tmp_path / "semantic-ir.json"
    artifact_path.write_text(
        json.dumps({"description": sentinel, "entities": []}), encoding="utf-8"
    )
    report = RunDiagnostics.initialize(tmp_path, entrypoint=entrypoint, clock=_clock())
    report.record_artifact(
        artifact_path,
        kind="semantic-ir",
        phase="validation",
        counts={"entities": 0},
    )
    report.finish(status="success")

    persisted = json.loads(
        (tmp_path / "run-diagnostics.json").read_text(encoding="utf-8")
    )
    schema = json.loads(
        (SKILL_DIR / "run-diagnostics.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(persisted)
    serialized = json.dumps(persisted, sort_keys=True)
    assert sentinel not in serialized
    assert "PROMPT_SECRET" not in serialized
    assert "IR_PROSE_SECRET" not in serialized
    assert "environment" not in persisted
    assert persisted["run"]["status"] == "success"
    assert persisted["run"]["total_ms"] == 0


def test_iterator_summary_is_fixed_size_schema_valid_and_privacy_bounded(
    tmp_path: Path,
) -> None:
    """Copying iterator source or validation values into shared diagnostics is a leak."""

    sentinel = "PRIVATE_UNIT_TEXT SOURCE_PROSE INVENTORY_PROSE RAW_INSTANCE_VALUE"
    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    timing = {"samples": 3, "total": 9, "maximum": 5}
    report.record_iterator_summary(
        {
            "setup": {
                "unit_count": 7,
                "worker_count": 2,
                "assigned_characters": 321,
                "internal_timings_ms": {
                    "scan": 1,
                    "unitization": 2,
                    "partition": 3,
                    "database": 4,
                    "validation": 5,
                    "total": 15,
                },
                "unit_text": sentinel,
            },
            "next": {
                "calls": 3,
                "acknowledgements": 2,
                "wraps": 1,
                "retries": 1,
                "failures": 1,
                "open_sequence": {
                    "count": 1,
                    "unit_count": 2,
                    "character_count": 88,
                    "maximum_elapsed_ms": 40,
                },
                "internal_timings_ms": {
                    name: timing
                    for name in (
                        "validation",
                        "transaction",
                        "lookup",
                        "serialization",
                        "total",
                    )
                },
                "source_prose": sentinel,
                "inventory_prose": sentinel,
                "raw_validation_instance": {"description": sentinel},
            },
        }
    )
    report.record_iterator_controller_timing(
        "setup",
        process_dispatch_ms=6,
        publication_ms=2,
        total_ms=23,
    )
    report.record_iterator_controller_timing(
        "setup",
        process_dispatch_ms=4,
        total_ms=10,
    )
    report.record_iterator_controller_timing(
        "next",
        process_dispatch_ms=7,
        total_ms=12,
    )

    persisted = json.loads(report.path.read_text(encoding="utf-8"))
    schema = json.loads(
        (SKILL_DIR / "run-diagnostics.schema.json").read_text(encoding="utf-8")
    )
    jsonschema.Draft202012Validator(schema).validate(persisted)
    assert persisted["iterator"]["setup"] == {
        "unit_count": 7,
        "worker_count": 2,
        "assigned_characters": 321,
        "internal_timings_ms": {
            "scan": 1,
            "unitization": 2,
            "partition": 3,
            "database": 4,
            "validation": 5,
            "total": 15,
        },
        "controller_timings_ms": {
            "process_dispatch": {"samples": 2, "total": 10, "maximum": 6},
            "publication": {"samples": 1, "total": 2, "maximum": 2},
            "total": {"samples": 2, "total": 33, "maximum": 23},
        },
    }
    assert persisted["iterator"]["next"]["controller_timings_ms"] == {
        "process_dispatch": {"samples": 1, "total": 7, "maximum": 7},
        "total": {"samples": 1, "total": 12, "maximum": 12},
    }
    assert sentinel not in json.dumps(persisted, sort_keys=True)


def test_failed_atomic_update_preserves_last_complete_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Publishing partial diagnostics after a write failure destroys recovery evidence."""

    real_writer = diagnostics.write_json_atomic
    calls = 0

    def fail_after_initialize(payload: dict, path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls > 1:
            raise OSError("injected diagnostics write failure")
        real_writer(payload, path)

    monkeypatch.setattr(diagnostics, "write_json_atomic", fail_after_initialize)
    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    before = (tmp_path / "run-diagnostics.json").read_bytes()

    with pytest.raises(OSError, match="injected diagnostics write failure"):
        report.worker_queued("inventory-001", phase="inventory", model="model-x")

    assert (tmp_path / "run-diagnostics.json").read_bytes() == before
    assert report.payload["jobs"] == []


def test_machine_interface_records_each_worker_and_finish_operation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A class-only lifecycle API would leave the gateway without a process route."""

    entrypoint = _entrypoint(tmp_path)
    output = tmp_path / "inventory-001.json"
    output.write_text('{"candidates": []}\n', encoding="utf-8")
    diagnostics.main(
        ["initialize", str(tmp_path), "--entrypoint", str(entrypoint)]
    )
    diagnostics.main(
        [
            "worker-queued",
            str(tmp_path),
            "inventory-001",
            "--phase",
            "inventory",
            "--model",
            "model-x",
        ]
    )
    diagnostics.main(["worker-started", str(tmp_path), "inventory-001"])
    diagnostics.main(
        [
            "worker-finished",
            str(tmp_path),
            "inventory-001",
            "--status",
            "success",
            "--output",
            str(output),
        ]
    )
    summary = diagnostics.main(["finish", str(tmp_path), "--status", "success"])

    assert summary["diagnostics"] == str(tmp_path / "run-diagnostics.json")
    assert summary["status"] == "success"
    assert summary["jobs"] == 1
    assert RunDiagnostics.open(tmp_path).payload["jobs"][0]["status"] == "success"
    assert "run-diagnostics.json" in capsys.readouterr().out


def test_invalid_event_and_secret_bearing_error_do_not_corrupt_or_leak(
    tmp_path: Path,
) -> None:
    """Invalid state must not publish, and arbitrary errors must only be hashed."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    before = (tmp_path / "run-diagnostics.json").read_bytes()
    with pytest.raises(jsonschema.ValidationError):
        report.worker_queued("bad-001", phase="unknown", model="model-x")
    assert (tmp_path / "run-diagnostics.json").read_bytes() == before
    assert report.payload["jobs"] == []

    report.worker_queued("extract-001", phase="extract", model="model-y")
    report.worker_finished(
        "extract-001",
        status="failure",
        error=RuntimeError("authorization token=top-secret-value"),
    )
    serialized = json.dumps(report.payload)
    assert "top-secret-value" not in serialized
    assert "authorization token" not in serialized
    assert hashlib.sha256(b"authorization token=top-secret-value").hexdigest() in serialized


def test_arbitrary_retry_worker_stage_and_schema_text_never_persists(
    tmp_path: Path,
) -> None:
    """Every text-bearing failure boundary must persist only stable structure and hashes."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    before = (tmp_path / "run-diagnostics.json").read_bytes()
    with pytest.raises(ValueError, match="retry code"):
        report.worker_queued(
            "extract-001",
            phase="extract",
            model="model-y",
            retry_code="RETRY_REASON_SENTINEL",
        )
    assert (tmp_path / "run-diagnostics.json").read_bytes() == before

    report.worker_queued("extract-001", phase="extract", model="model-y")
    report.worker_finished(
        "extract-001",
        status="failure",
        error=RuntimeError("WORKER_EXCEPTION_SENTINEL"),
    )
    with pytest.raises(RuntimeError, match="STAGE_EXCEPTION_SENTINEL"):
        with report.stage("rendering"):
            raise RuntimeError("STAGE_EXCEPTION_SENTINEL")
    with pytest.raises(jsonschema.ValidationError):
        with report.stage("validation", validation=True):
            raise jsonschema.ValidationError(
                "SCHEMA_INSTANCE_SENTINEL",
                validator="type",
                path=["entities", 0, "description"],
            )

    serialized = json.dumps(report.payload, sort_keys=True)
    for sentinel in (
        "RETRY_REASON_SENTINEL",
        "WORKER_EXCEPTION_SENTINEL",
        "STAGE_EXCEPTION_SENTINEL",
        "SCHEMA_INSTANCE_SENTINEL",
    ):
        assert sentinel not in serialized
    validation_diagnostic = report.payload["stages"][-1]["diagnostic"]
    assert validation_diagnostic["code"] == "validation-failed"
    assert validation_diagnostic["schema_keyword"] == "type"
    assert validation_diagnostic["schema_path"] == ["entities", 0, "description"]
    assert report.payload["counts"]["validation_errors"] == 1


@pytest.mark.parametrize(
    "operation_args",
    (
        [
            "worker-queued",
            "{run_dir}",
            "extract-001",
            "--phase",
            "extract",
            "--model",
            "model-y",
            "--retry-code",
            "CLI_RETRY_SENTINEL",
        ],
        [
            "finish",
            "{run_dir}",
            "--status",
            "failure",
            "--error-code",
            "CLI_ERROR_SENTINEL",
        ],
    ),
)
def test_machine_interface_rejects_unrestricted_retry_and_error_text(
    tmp_path: Path, operation_args: list[str]
) -> None:
    """The process boundary must accept only allowlisted retry and failure codes."""

    RunDiagnostics.initialize(tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock())
    before = (tmp_path / "run-diagnostics.json").read_bytes()
    argv = [str(tmp_path) if value == "{run_dir}" else value for value in operation_args]

    with pytest.raises(SystemExit):
        diagnostics.main(argv)

    assert (tmp_path / "run-diagnostics.json").read_bytes() == before


def test_artifact_measurement_streams_bounded_chunks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Size and SHA measurement must never request an artifact's complete byte string."""

    content = b"x" * (diagnostics._HASH_CHUNK_SIZE * 2 + 17)
    reads: list[int] = []

    class GuardedStream(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            reads.append(size)
            assert 0 < size <= diagnostics._HASH_CHUNK_SIZE
            return super().read(size)

    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: GuardedStream(content))
    size, digest = diagnostics._measure_file(tmp_path / "opaque-artifact")

    assert size == len(content)
    assert digest == hashlib.sha256(content).hexdigest()
    assert reads[-1] == diagnostics._HASH_CHUNK_SIZE


def test_initialize_rejects_existing_report_without_erasing_history(tmp_path: Path) -> None:
    """Re-initialization must not replace durable events in an existing run directory."""

    entrypoint = _entrypoint(tmp_path)
    report = RunDiagnostics.initialize(tmp_path, entrypoint=entrypoint, clock=_clock())
    report.worker_queued("inventory-001", phase="inventory", model="model-x")
    before = (tmp_path / "run-diagnostics.json").read_bytes()

    with pytest.raises(FileExistsError, match="already exists"):
        RunDiagnostics.initialize(tmp_path, entrypoint=entrypoint, clock=_clock())

    assert (tmp_path / "run-diagnostics.json").read_bytes() == before


def test_initialize_records_measurable_setup_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Entrypoint measurement belongs to initialization rather than an invisible gap."""

    clock = _clock()
    real_measure = diagnostics._measure_file

    def measured(path: Path) -> tuple[int, str]:
        result = real_measure(path)
        clock.advance(milliseconds=17)
        return result

    monkeypatch.setattr(diagnostics, "_measure_file", measured)
    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=clock
    )

    assert report.payload["run"]["initialization_ms"] == 17
    assert report.payload["run"]["initialized_at_utc"] == (
        "2026-08-19T12:00:00.017000Z"
    )


def test_artifact_records_are_deduplicated_by_stable_identity(tmp_path: Path) -> None:
    """Repeated phase instrumentation must reference one unchanged measured artifact."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    artifact = tmp_path / "inventory.json"
    artifact.write_bytes(b"inventory")

    first = report.record_artifact(artifact, kind="pooled-inventory", phase="pooling")
    second = report.record_artifact(artifact, kind="pooled-inventory", phase="pooling")

    assert second == first
    assert report.payload["artifacts"] == [first]


@pytest.mark.parametrize("active_state", ("queued", "running", "failure"))
def test_success_finish_rejects_incomplete_or_unrecovered_jobs(
    tmp_path: Path, active_state: str
) -> None:
    """A successful run cannot coexist with active or latest-failed worker attempts."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    report.worker_queued("inventory-001", phase="inventory", model="model-x")
    if active_state in {"running", "failure"}:
        report.worker_started("inventory-001")
    if active_state == "failure":
        report.worker_finished(
            "inventory-001", status="failure", error=RuntimeError("private")
        )

    with pytest.raises(ValueError, match="cannot finish successfully"):
        report.finish(status="success")
    assert report.payload["run"]["status"] == "running"


@pytest.mark.parametrize("active_state", ("queued", "running"))
def test_retry_rejects_an_active_prior_attempt(
    tmp_path: Path, active_state: str
) -> None:
    """A retry must not hide an earlier queued or running attempt for the same job."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    report.worker_queued("inventory-001", phase="inventory", model="model-x")
    if active_state == "running":
        report.worker_started("inventory-001")
    before = (tmp_path / "run-diagnostics.json").read_bytes()

    with pytest.raises(ValueError, match="active prior attempt"):
        report.worker_queued(
            "inventory-001",
            phase="inventory",
            model="model-x",
            retry_code="transient",
        )

    assert (tmp_path / "run-diagnostics.json").read_bytes() == before
    assert len(report.payload["jobs"]) == 1


def test_retry_requires_an_allowlisted_reason_code(tmp_path: Path) -> None:
    """Every attempt after the first must explain its retry with a stable code."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    report.worker_queued("inventory-001", phase="inventory", model="model-x")
    report.worker_finished(
        "inventory-001", status="failure", error=RuntimeError("private")
    )
    before = (tmp_path / "run-diagnostics.json").read_bytes()

    with pytest.raises(ValueError, match="retry code is required"):
        report.worker_queued("inventory-001", phase="inventory", model="model-x")

    assert (tmp_path / "run-diagnostics.json").read_bytes() == before


@pytest.mark.parametrize("hidden_state", ("queued", "running"))
def test_success_finish_checks_every_attempt_not_only_latest_by_job_id(
    tmp_path: Path, hidden_state: str
) -> None:
    """A later success must not conceal a nonterminal earlier attempt in durable state."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    report.worker_queued("inventory-001", phase="inventory", model="model-x")
    report.worker_finished(
        "inventory-001", status="failure", error=RuntimeError("private")
    )
    report.worker_queued(
        "inventory-001",
        phase="inventory",
        model="model-x",
        retry_code="worker-failed",
    )
    report.worker_started("inventory-001")
    output = tmp_path / "inventory-001.json"
    output.write_text('{"candidates": []}\n', encoding="utf-8")
    report.worker_finished("inventory-001", status="success", output=output)

    hidden = deepcopy(report.payload["jobs"][1])
    hidden["status"] = hidden_state
    hidden.pop("finished_at_utc", None)
    hidden.pop("worker_ms", None)
    hidden.pop("output_artifact", None)
    if hidden_state == "queued":
        hidden.pop("started_at_utc", None)
        hidden.pop("started_monotonic_ns", None)
        hidden.pop("queue_ms", None)
    report.payload["jobs"].insert(1, hidden)
    report.payload["counts"]["jobs"] += 1

    with pytest.raises(ValueError, match="nonterminal worker attempt"):
        report.finish(status="success")

    assert report.payload["run"]["status"] == "running"


def test_success_finish_rejects_a_running_deterministic_stage(tmp_path: Path) -> None:
    """A run cannot succeed while deterministic stage instrumentation is open."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    with report.stage("planning"):
        with pytest.raises(ValueError, match="nonterminal deterministic stage"):
            report.finish(status="success")

    assert report.payload["run"]["status"] == "running"
    assert report.payload["stages"][0]["status"] == "success"


def test_finish_is_terminal_until_explicit_failure_resume(tmp_path: Path) -> None:
    """Double finish must fail, while explicit failure recovery keeps its history."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    report.finish(status="failure", error=RuntimeError("first private failure"))
    with pytest.raises(ValueError, match="already finished"):
        report.finish(status="failure", error=RuntimeError("duplicate"))

    report.resume()
    report.finish(status="success")
    run = report.payload["run"]
    assert run["status"] == "success"
    assert len(run["failure_history"]) == 1
    assert "first private failure" not in json.dumps(run)


def test_summary_includes_latest_stage_status_and_duration(tmp_path: Path) -> None:
    """The process summary must expose the latest stage outcome, not just its name."""

    clock = _clock()
    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=clock
    )
    with report.stage("planning"):
        clock.advance(milliseconds=41)

    assert diagnostics._summary(report)["latest_stage"] == {
        "operation": "planning",
        "status": "success",
        "duration_ms": 41,
    }


def test_success_requires_started_worker_and_measurable_output(tmp_path: Path) -> None:
    """Skipping started or output would make successful worker timings incomplete."""

    report = RunDiagnostics.initialize(
        tmp_path, entrypoint=_entrypoint(tmp_path), clock=_clock()
    )
    output = tmp_path / "inventory-001.json"
    output.write_text('{"candidates": []}\n', encoding="utf-8")
    report.worker_queued("inventory-001", phase="inventory", model="model-x")
    with pytest.raises(ValueError, match="running diagnostics attempt"):
        report.worker_finished("inventory-001", status="success", output=output)
    assert report.payload["jobs"][0]["status"] == "queued"

    report.worker_started("inventory-001")
    with pytest.raises(ValueError, match="requires an output artifact"):
        report.worker_finished("inventory-001", status="success")
    assert report.payload["jobs"][0]["status"] == "running"


def test_phase_driver_rendered_html_provenance_names_renderer_inputs() -> None:
    """HTML provenance must name the three generated JSON artifacts rendered."""

    driver_blueprint = yaml.safe_load(
        (
            SKILL_DIR
            / "_rtx"
            / "blueprints"
            / "rtx-extraction-phase-driver.yaml"
        ).read_text(encoding="utf-8")
    )
    driver_interface = next(iter(driver_blueprint["interfaces"].values()))
    reads = {
        item["id"]: item
        for item in driver_interface["contract"]["direct_io"]["reads"]
    }
    assert reads["read-5"] == {
        "id": "read-5",
        "medium": "local-filesystem",
        "access": "read",
        "content": (
            "validated semantic IR, compiled dependency graph, and extracted "
            "MathJax macros consumed directly by final rendering"
        ),
        "formats": ["json"],
        "path": (
            "<run-dir>/{semantic-ir.json,dependency-graph.json,"
            "mathjax-macros.json}"
        ),
        "path_match": "glob",
        "sensitivity": "derived-private",
        "system": "filesystem",
    }
    effects = {
        effect["id"]: effect
        for effect in driver_interface["contract"]["execution"]["effects"]
    }
    assert effects["rendered-html"]["value_source"] == {
        "kind": "direct-io",
        "direct_io_ref": "read-5",
    }


def test_authored_blueprints_register_diagnostics_and_propagate_versions() -> None:
    """An unregistered report owner cannot be authorized through the skill facade."""

    parent = yaml.safe_load((SKILL_DIR / "blueprint.yaml").read_text(encoding="utf-8"))
    runtime = yaml.safe_load(
        (SKILL_DIR / "_rtx" / "blueprint.yaml").read_text(encoding="utf-8")
    )
    diagnostics_blueprint = yaml.safe_load(
        (
            SKILL_DIR
            / "_rtx"
            / "blueprints"
            / "rtx-run-diagnostics.yaml"
        ).read_text(encoding="utf-8")
    )
    driver_blueprint = yaml.safe_load(
        (
            SKILL_DIR
            / "_rtx"
            / "blueprints"
            / "rtx-extraction-phase-driver.yaml"
        ).read_text(encoding="utf-8")
    )
    source_id = "math-dependency-graph._rtx.source.rtx-run-diagnostics"
    facade_id = "math-dependency-graph._rtx.interface.scripts-record-run-diagnostics"
    source_interface = (
        f"{source_id}.interface.scripts-record-run-diagnostics"
    )

    assert "run-diagnostics\\.schema\\.json" in "\n".join(parent["content"])
    assert runtime["sources"][source_id]["blueprint"]["path"] == (
        "blueprints/rtx-run-diagnostics.yaml"
    )
    assert runtime["exports"][facade_id]["source_interface"] == source_interface
    assert "_run_diagnostics\\.py" in runtime["content"]
    assert any(
        permission["command"][-2:] == ["_run_diagnostics.py", "Interface"]
        for permission in runtime["authority"]["suggested_permissions"]["bash"]
    )
    namespace = parent["namespace_exports"]["_rtx"]
    assert namespace["version"] == runtime["version"]
    interface = diagnostics_blueprint["interfaces"][source_interface]
    assert namespace["surface"]["only"][facade_id] == interface["version"]
    assert facade_id in namespace["interface_access"]

    patterns = {
        pattern["name"]: pattern
        for pattern in interface["process_binding"]["patterns"]
    }
    assert set(patterns) == {
        "initialize",
        "worker-queued",
        "worker-started",
        "worker-finished",
        "iterator-controller-timing-setup",
        "iterator-controller-timing-next",
        "finish",
    }
    assert interface["usage"] == (
        "<initialize|worker-queued|worker-started|worker-finished|"
        "iterator-controller-timing|finish> "
        "<run-dir> [<job-id>] [operation flags]"
    )
    assert diagnostics_blueprint["runtime_dependencies"] == []
    assert {dependency["source"] for dependency in diagnostics_blueprint["dependencies"]} == {
        "math-dependency-graph._rtx.source.rtx-init",
        "math-dependency-graph._rtx.source.rtx-batch-ir-merger",
    }
    driver_dependency = next(
        dependency
        for dependency in driver_blueprint["dependencies"]
        if dependency["source"] == source_id
    )
    assert driver_dependency["version"] == diagnostics_blueprint["version"]
    driver_interface = next(iter(driver_blueprint["interfaces"].values()))
    reads = driver_interface["contract"]["direct_io"]["reads"]
    writes = driver_interface["contract"]["direct_io"]["writes"]
    assert any(item.get("path") == "<run-dir>/run-diagnostics.json" for item in reads)
    assert any(item.get("path") == "<run-dir>/run-diagnostics.json" for item in writes)
    assert "non_idempotent" in interface["contract"]["execution"]["mutation_safety"][
        "idempotency"
    ]
    assert "required" in interface["contract"]["arguments"]["output"][
        "description"
    ].lower()
    driver_idempotency = driver_interface["contract"]["execution"][
        "mutation_safety"
    ]["idempotency"]
    assert "non_idempotent" in driver_idempotency
    assert "appends" in driver_idempotency["non_idempotent"]
    effects = {
        effect["id"]: effect
        for effect in driver_interface["contract"]["execution"]["effects"]
    }
    assert effects["diagnostics-update"]["direct_io_ref"] == "write-4"
    assert set(effects["diagnostics-update"]["may_occur_in_outcomes"]) == {
        "advanced",
        "invalid",
    }
    outcomes = {
        outcome["id"]: outcome
        for outcome in driver_interface["contract"]["outcomes"]
    }
    assert "diagnostics-update" in outcomes["advanced"]["effects"]
    assert "diagnostics-update" in outcomes["invalid"]["effects"]
    assert effects["rendered-html"]["direct_io_ref"] == "write-2"
    assert effects["rendered-html"]["confirmation_evidence"] == {
        "kind": "direct-io",
        "direct_io_ref": "write-2",
    }
    html_argument = driver_interface["contract"]["arguments"]["html"]
    assert "<run-dir>/dependency-graph.html" in html_argument["description"]
    rendered_html_write = next(item for item in writes if item["id"] == "write-2")
    assert rendered_html_write["path"] == (
        "<html> when supplied; otherwise <run-dir>/dependency-graph.html"
    )
    assert "derived from validated semantic artifacts" in rendered_html_write["content"]
    assert "reversible" in effects["rendered-html"]["reversibility"]
    assert set(effects["rendered-html"]["may_occur_in_outcomes"]) == {
        "advanced",
        "invalid",
    }
    assert "rendered-html" in outcomes["advanced"]["effects"]
    assert "rendered-html" in outcomes["invalid"]["effects"]
