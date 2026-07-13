from __future__ import annotations

import argparse
import importlib
import json
import subprocess
import sys
from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]
REPO_SRC = SKILL_ROOT.parents[1] / "src"
RUNTIME_PATH = SKILL_ROOT / "_rtx" / "_mail_envelope_stream.py"
SIDECAR_PATH = SKILL_ROOT / "_rtx" / "._mail_envelope_stream.py.blueprint.yaml"


def _load_runtime():
    assert RUNTIME_PATH.is_file(), "composite runtime is missing"
    repo_src = str(REPO_SRC)
    if repo_src not in sys.path:
        sys.path.insert(0, repo_src)
    skill_path = str(SKILL_ROOT)
    if skill_path not in sys.path:
        sys.path.insert(0, skill_path)
    for name in ("_rtx._mail_envelope_stream", "_rtx._envelope_gate"):
        sys.modules.pop(name, None)
    return importlib.import_module("_rtx._mail_envelope_stream")


def _isolate_filter_state(module, tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "last_run").write_text("2026-07-05T10:00:00-04:00", encoding="utf-8")
    module.envelope_gate.STATE_DIR = state_dir
    module.envelope_gate.WATERMARK = state_dir / "last_run"
    module.envelope_gate.STATUS_FILE = state_dir / "status.json"


def test_composite_dispatches_mail_list_and_emits_only_filtered_envelopes(
    tmp_path: Path, capsys
) -> None:
    module = _load_runtime()
    _isolate_filter_state(module, tmp_path)
    unfiltered = [
        {
            "id": "old",
            "flags": [],
            "subject": "must stay private",
            "from": "old@example.com",
            "date": "2026-07-05T09:00:00-04:00",
            "message_id": "<old>",
        },
        {
            "id": "new",
            "flags": ["\\Seen"],
            "subject": "visible",
            "from": "new@example.com",
            "date": "2026-07-05T11:00:00-04:00",
            "message_id": "<new>",
        },
    ]

    class RecordingInterface(module.Interface):
        def __init__(self) -> None:
            self.calls = []

        def dispatch(self, key, **kwargs):
            self.calls.append((key, kwargs))
            return subprocess.CompletedProcess([], 0, json.dumps(unfiltered), "")

    interface = RecordingInterface()
    result = interface.run(argparse.Namespace(account="work", after="2026-07-04"))
    captured = capsys.readouterr()

    assert result == 0
    assert interface.calls == [
        (
            "mail-list",
            {
                "args": ["-a", "work", "--after", "2026-07-04"],
                "capture_output": True,
                "text": True,
            },
        )
    ]
    assert [envelope["id"] for envelope in json.loads(captured.out)] == ["new"]
    assert "must stay private" not in captured.out


def test_composite_returns_existing_no_new_email_message(tmp_path: Path, capsys) -> None:
    module = _load_runtime()
    _isolate_filter_state(module, tmp_path)

    class EmptyInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            return subprocess.CompletedProcess([], 0, "[]", "")

    result = EmptyInterface().run(argparse.Namespace(account="work", after="2026-07-04"))
    captured = capsys.readouterr()

    assert result == 0
    assert captured.out.startswith("(no new emails for work since ")


def test_composite_dispatch_failure_does_not_emit_raw_payload(capsys) -> None:
    module = _load_runtime()
    raw_payload = '[{"id":"private","subject":"dispatch secret"}]'

    class FailedInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            return subprocess.CompletedProcess([], 7, raw_payload, "")

    result = FailedInterface().run(argparse.Namespace(account="work", after="2026-07-04"))
    captured = capsys.readouterr()

    assert result == 7
    assert captured.out == ""
    assert captured.err == "error: mail-list failed with exit code 7\n"
    assert raw_payload not in captured.out + captured.err


def test_composite_invalid_json_does_not_emit_raw_payload(capsys) -> None:
    module = _load_runtime()
    raw_payload = '{"subject":"invalid secret" trailing'

    class InvalidJsonInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            return subprocess.CompletedProcess([], 0, raw_payload, "")

    result = InvalidJsonInterface().run(
        argparse.Namespace(account="work", after="2026-07-04")
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert captured.err == "error: mail-list returned invalid envelope JSON\n"
    assert raw_payload not in captured.out + captured.err


def test_composite_non_list_json_does_not_emit_raw_payload(capsys) -> None:
    module = _load_runtime()
    raw_payload = '{"subject":"non-list secret"}'

    class NonListInterface(module.Interface):
        def dispatch(self, key, **kwargs):
            return subprocess.CompletedProcess([], 0, raw_payload, "")

    result = NonListInterface().run(
        argparse.Namespace(account="work", after="2026-07-04")
    )
    captured = capsys.readouterr()

    assert result == 1
    assert captured.out == ""
    assert captured.err == "error: mail-list returned invalid envelope JSON\n"
    assert raw_payload not in captured.out + captured.err


def test_composite_declares_typed_dispatch_boundary() -> None:
    assert SIDECAR_PATH.is_file(), "composite machine sidecar is missing"
    sidecar = yaml.safe_load(SIDECAR_PATH.read_text(encoding="utf-8"))
    root = yaml.safe_load((SKILL_ROOT / "blueprint.yaml").read_text(encoding="utf-8"))
    module = _load_runtime()

    call = module.Interface.dispatches["mail-list"]
    assert (call.caller_skill, call.target_skill, call.interface) == (
        "email-triage",
        "email-client",
        "mail-list",
    )
    assert sidecar["id"] == "email-triage.machine.fetch-filtered-envelopes"
    assert sidecar["binding"] == {
        "kind": "python-entrypoint",
        "path": "_rtx/_mail_envelope_stream.py",
        "symbol": "Interface",
    }
    assert sidecar["uses_interfaces"] == [
        {"interface": "email-client.machine.mail-list", "version": 1}
    ]
    assert sidecar["platform_support"] == {
        "linux": True,
        "macos": True,
        "windows": True,
    }
    assert sidecar["direct_io"]["writes"][0]["medium"] == "stdout"
    assert sidecar["owns_filesystem"] == []
    assert any(
        entry["interface"] == "email-triage.machine.fetch-filtered-envelopes"
        and entry["blueprint"]["path"] == "_rtx/._mail_envelope_stream.py.blueprint.yaml"
        for entry in root["interfaces"]
    )

    email_client = yaml.safe_load(
        (SKILL_ROOT.parent / "email-client" / "blueprint.yaml").read_text(encoding="utf-8")
    )
    assert "email-triage" in email_client["interfaces"]["machine"]["mail-list"]["allowed_callers"]
