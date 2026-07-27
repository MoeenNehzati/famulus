from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from test_support.runtime_module import load_runtime_module

SKILL_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PATH = SKILL_ROOT / "_mail_envelope_stream.py"


def _load_runtime():
    assert RUNTIME_PATH.is_file(), "composite runtime is missing"
    return load_runtime_module(RUNTIME_PATH)


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


def test_composite_declares_mail_list_dispatch_boundary() -> None:
    module = _load_runtime()

    call = module.Interface.dispatches["mail-list"]
    assert call.caller_skill in {"email-triage", "email-triage-rtx"}
    assert (call.target_skill, call.interface) == (
        "email-client",
        "mail-list",
    )
