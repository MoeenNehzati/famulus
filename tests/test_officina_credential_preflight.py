from __future__ import annotations

import base64
import importlib.util
import json
import os
import pickle
import signal
import subprocess
import sys
import textwrap
import time
import types
from dataclasses import dataclass
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from officina.install.credential_preflight import (  # noqa: E402
    CredentialVerificationResult,
    CredentialWorkerCode,
    CredentialWorkerError,
    ManagedCredentialWorker,
    CredentialPreflightCode,
    CredentialPreflightResult,
    main,
    probe_native_store,
)
from officina.install import credential_preflight as preflight  # noqa: E402
from officina.install import (  # noqa: E402
    credential_preflight_linux_osx_windows as containment,
)


class PositivePlaintextBackend:
    __module__ = "keyrings.alt.file"
    name = "plaintext"

    def __init__(self) -> None:
        self.writes: list[tuple[str, str, str]] = []

    def store(self, namespace: str, key: str, secret: str) -> None:
        self.writes.append((namespace, key, secret))

    def lookup(self, namespace: str, key: str) -> str | None:
        return None

    def clear(self, namespace: str, key: str) -> bool:
        return True


class ChainerBackend(PositivePlaintextBackend):
    __module__ = "keyring.backends.chainer"


class CustomBackend(PositivePlaintextBackend):
    __module__ = "third_party.credentials"


@pytest.mark.parametrize(
    "backend", [PositivePlaintextBackend(), ChainerBackend(), CustomBackend()]
)
def test_preflight_rejects_non_native_backend_even_when_roundtrip_works(backend) -> None:
    result = probe_native_store(backend=backend, token_factory=lambda: "unused")

    assert result.code is CredentialPreflightCode.UNSUPPORTED_BACKEND
    assert backend.writes == []


class Keyring:
    """Configurable native-name backend used to pressure the probe protocol."""

    __module__ = {
        "linux": "keyring.backends.SecretService",
        "darwin": "keyring.backends.macOS",
        "win32": "keyring.backends.Windows",
    }.get(sys.platform, "unsupported")

    def __init__(
        self,
        *,
        mode: str = "normal",
        marker: Path | None = None,
        pid_log: Path | None = None,
        collision_marker: Path | None = None,
        lookup_log: Path | None = None,
        descendant_pid: Path | None = None,
    ) -> None:
        self.mode = mode
        self.marker = marker
        self.pid_log = pid_log
        self.collision_marker = collision_marker
        self.lookup_log = lookup_log
        self.descendant_pid = descendant_pid
        self.values: dict[tuple[str, str], str] = {}

    def _record_pid(self, operation: str) -> None:
        if self.pid_log is not None:
            with self.pid_log.open("a", encoding="utf-8") as stream:
                stream.write(f"{operation}:{os.getpid()}\n")

    def store(self, namespace: str, key: str, secret: str) -> None:
        self._record_pid("store")
        if self.marker is not None:
            self.marker.write_text("present", encoding="utf-8")
        self.values[(namespace, key)] = secret
        if self.mode == "leak-exception":
            print(secret)
            print(secret, file=sys.stderr)
            raise RuntimeError(f"backend rejected {secret}")
        if self.mode == "kill-after-store":
            os._exit(23)
        if self.mode == "hang-after-store":
            time.sleep(60)
        if self.mode == "descendant-hang" and self.descendant_pid is not None:
            subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    (
                        "import os,time;"
                        f"open({str(self.descendant_pid)!r},'w').write(str(os.getpid()));"
                        "time.sleep(60)"
                    ),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            deadline = time.monotonic() + 2
            while not self.descendant_pid.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(60)

    def lookup(self, namespace: str, key: str) -> str | None:
        self._record_pid("lookup")
        if self.lookup_log is not None:
            with self.lookup_log.open("a", encoding="utf-8") as stream:
                stream.write(f"{key}\n")
        if self.mode == "collision-once" and self.collision_marker is not None:
            if not self.collision_marker.exists():
                self.collision_marker.write_text(key, encoding="utf-8")
                return "existing"
            if self.collision_marker.read_text(encoding="utf-8") == key:
                return "existing"
        if self.mode == "kill-during-check" and self.marker is not None:
            crash_marker = self.marker.with_suffix(".crashed")
            if not crash_marker.exists():
                crash_marker.write_text("attempted", encoding="utf-8")
                self.marker.write_text("present", encoding="utf-8")
                os._exit(31)
        if self.mode == "locked":
            raise KeyringLocked("malicious backend detail")
        if self.mode == "unavailable":
            raise InitError("malicious backend detail")
        if self.mode == "absent-service":
            raise SecretServiceNotAvailableException("malicious backend detail")
        if self.mode == "permission-denied":
            raise PermissionError("malicious backend detail")
        if self.marker is not None and self.marker.exists():
            return self.values.get((namespace, key), "present")
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        self._record_pid("clear")
        existed = (namespace, key) in self.values or (
            self.marker is not None and self.marker.exists()
        )
        if self.mode == "false-delete":
            return False
        if self.mode == "retain-delete":
            return True
        if self.mode == "hang-delete":
            time.sleep(60)
        if self.mode == "hang-delete-once" and self.marker is not None:
            hang_marker = self.marker.with_suffix(".hung")
            if not hang_marker.exists():
                hang_marker.write_text("attempted", encoding="utf-8")
                time.sleep(60)
        self.values.pop((namespace, key), None)
        if self.marker is not None:
            self.marker.unlink(missing_ok=True)
        return existed


if sys.platform == "win32":
    Keyring.__name__ = "WinVaultKeyring"

_NATIVE_BACKEND_IDENTITY = f"{Keyring.__module__}.{Keyring.__name__}"
_TARGET_ONE = "native-preflight-" + "1" * 32
_TARGET_TWO = "native-preflight-" + "2" * 32


@pytest.fixture(autouse=True)
def install_canonical_native_backend_module(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.ModuleType(Keyring.__module__)
    setattr(module, Keyring.__name__, Keyring)
    monkeypatch.setitem(sys.modules, Keyring.__module__, module)


def test_allowlisted_name_spoof_is_rejected_before_store() -> None:
    class SpoofedNativeBackend(PositivePlaintextBackend):
        __module__ = Keyring.__module__

    SpoofedNativeBackend.__name__ = Keyring.__name__
    backend = SpoofedNativeBackend()

    result = probe_native_store(
        backend=backend,
        token_factory=fixed_secret("unused"),
        timeout_seconds=2,
    )

    assert result.code is CredentialPreflightCode.UNSUPPORTED_BACKEND
    assert backend.writes == []


class KeyringLocked(Exception):
    pass


class InitError(Exception):
    pass


class SecretServiceNotAvailableException(Exception):
    pass


@dataclass(frozen=True)
class FixedSecret:
    value: str

    def __call__(self) -> str:
        return self.value


def fixed_secret(value: str) -> FixedSecret:
    return FixedSecret(value)


def encoded_canaries(secret: str) -> set[str]:
    encoded = base64.b64encode(secret.encode("utf-8")).decode("ascii")
    return {
        secret,
        encoded,
        secret.encode("utf-8").hex(),
        json.dumps(secret)[1:-1],
        "-----BEGIN " + "PRIVATE KEY-----" + encoded + "-----END " + "PRIVATE KEY-----",
    }


def _load_runtime_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _persist_through_real_repository_sinks(
    tmp_path: Path,
    *,
    payload: dict[str, object],
    retained_output: str,
) -> tuple[Path, Path, Path]:
    """Exercise real durable install-record and retained-log writers.

    Task 3 does not wire preflight into the Phase 1 journal or manifest yet.
    Persist the exact closed result through their shared production serializer,
    then retain the real CLI output through the repository's run-log writer.
    """
    repository_root = Path(__file__).parents[1]
    state_record = _load_runtime_module(
        "credential_preflight_state_record_sink",
        repository_root / "skills/install-assistant-tools/_rtx/_state_record.py",
    )
    run_record = _load_runtime_module(
        "credential_preflight_retained_log_sink",
        repository_root / "skills/recurring-tasks/_rtx/_run_record.py",
    )
    state_root = tmp_path / "install-state"
    state_root.mkdir(mode=0o700)
    journal_path = state_root / "transaction-journal.json"
    manifest_path = state_root / "install-manifest.json"
    state_record._atomic_json_replace(journal_path, payload, state_root=state_root)
    state_record._atomic_json_replace(manifest_path, payload, state_root=state_root)

    log_dir = tmp_path / "retained-test-logs"
    record = run_record.JobRunRecord(
        job_name="credential-preflight-test",
        started_at="2026-08-12T00:00:00Z",
        finished_at="2026-08-12T00:00:01Z",
        process_exit_code=1,
        inner_status="error",
        success=False,
        reason=retained_output.rstrip("\n"),
        run_id="credential-preflight-test-run",
    )
    run_record.write_run_record(log_dir=log_dir, record=record)
    retained_log_path = log_dir / record.job_name / "latest.json"
    return journal_path, manifest_path, retained_log_path


def test_backend_exception_and_raw_streams_cannot_leak_probe_secret(
    capsys,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = 'CANARY-"\\' + "a" * 64

    result = probe_native_store(
        backend=Keyring(mode="leak-exception"),
        token_factory=fixed_secret(secret),
        timeout_seconds=2,
    )

    backend_streams = capsys.readouterr()
    monkeypatch.setattr(preflight, "probe_native_store", lambda: result)
    assert main(["--json"]) == 1
    cli_streams = capsys.readouterr()
    payload = result.as_json()
    sink_paths = _persist_through_real_repository_sinks(
        tmp_path,
        payload=payload,
        retained_output=cli_streams.out,
    )
    journal_path, manifest_path, retained_log_path = sink_paths
    assert json.loads(cli_streams.out) == payload
    assert json.loads(journal_path.read_bytes()) == payload
    assert json.loads(manifest_path.read_bytes()) == payload
    assert json.loads(retained_log_path.read_bytes())["reason"] == cli_streams.out.rstrip(
        "\n"
    )
    combined = (
        backend_streams.out.encode("utf-8")
        + backend_streams.err.encode("utf-8")
        + cli_streams.out.encode("utf-8")
        + cli_streams.err.encode("utf-8")
        + json.dumps(payload).encode("utf-8")
        + b"".join(sink_path.read_bytes() for sink_path in sink_paths)
    )
    assert result.code is CredentialPreflightCode.ROUNDTRIP_FAILED
    for canary in encoded_canaries(secret):
        assert canary.encode("utf-8") not in combined


@pytest.mark.parametrize("mode", ["false-delete", "retain-delete"])
def test_cleanup_requires_successful_delete_and_final_absence(mode: str) -> None:
    result = probe_native_store(
        backend=Keyring(mode=mode),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=2,
    )

    assert result.code is CredentialPreflightCode.CLEANUP_FAILED


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("locked", CredentialPreflightCode.BACKEND_LOCKED),
        ("unavailable", CredentialPreflightCode.BACKEND_UNAVAILABLE),
        ("absent-service", CredentialPreflightCode.BACKEND_UNAVAILABLE),
        ("permission-denied", CredentialPreflightCode.ROUNDTRIP_FAILED),
    ],
)
def test_backend_failures_map_to_closed_codes(mode: str, expected: CredentialPreflightCode) -> None:
    result = probe_native_store(
        backend=Keyring(mode=mode),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=2,
    )

    assert result.code is expected
    assert set(result.as_json()) == {"schema_version", "ok", "code", "backend"}


def test_probe_regenerates_target_after_collision(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    targets = iter(["occupied", "available"])
    seen_path = tmp_path / "seen-targets"

    monkeypatch.setattr(
        "officina.install.credential_preflight._new_target_id",
        lambda: f"native-preflight-{next(targets)}",
    )

    result = probe_native_store(
        backend=Keyring(
            mode="collision-once",
            collision_marker=tmp_path / "collision-marker",
            lookup_log=seen_path,
        ),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=2,
    )

    assert result.ok
    assert seen_path.read_text(encoding="utf-8").splitlines()[:2] == [
        "native-preflight-occupied",
        "native-preflight-available",
    ]


def test_abnormal_exit_after_store_runs_separate_cleanup_child(tmp_path: Path) -> None:
    marker = tmp_path / "credential-present"
    pid_log = tmp_path / "pids.log"

    result = probe_native_store(
        backend=Keyring(mode="kill-after-store", marker=marker, pid_log=pid_log),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=2,
    )

    assert result.code is CredentialPreflightCode.ROUNDTRIP_FAILED
    assert not marker.exists()
    pids = {
        int(line.split(":", 1)[1])
        for line in pid_log.read_text(encoding="utf-8").splitlines()
    }
    assert os.getpid() not in pids
    assert len(pids) >= 2


def test_timeout_during_delete_is_killed_then_cleaned_by_new_child(tmp_path: Path) -> None:
    marker = tmp_path / "credential-present"
    started = time.monotonic()

    result = probe_native_store(
        backend=Keyring(mode="hang-delete-once", marker=marker),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=0.2,
    )

    assert result.code is CredentialPreflightCode.ROUNDTRIP_FAILED
    assert time.monotonic() - started < 3
    assert not marker.exists()


def test_cleanup_child_timeout_fails_closed_and_is_bounded(tmp_path: Path) -> None:
    marker = tmp_path / "credential-present"
    started = time.monotonic()

    result = probe_native_store(
        backend=Keyring(mode="hang-delete", marker=marker),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=0.2,
    )

    assert result.code is CredentialPreflightCode.CLEANUP_FAILED
    assert time.monotonic() - started < 3
    assert marker.exists()


def test_success_is_closed_json_after_verified_absence() -> None:
    result = probe_native_store(
        backend=Keyring(),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=2,
    )

    assert result == CredentialPreflightResult(
        schema_version=1,
        ok=True,
        code=None,
        backend=_NATIVE_BACKEND_IDENTITY,
    )
    assert result.as_json() == {
        "schema_version": 1,
        "ok": True,
        "code": None,
        "backend": _NATIVE_BACKEND_IDENTITY,
    }


def test_json_cli_uses_real_production_child_boundary(capsys) -> None:
    exit_code = main(["--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err == ""
    assert set(payload) == {"schema_version", "ok", "code", "backend"}
    assert payload["schema_version"] == 1
    assert exit_code == (0 if payload["ok"] else 1)
    if payload["code"] is not None:
        assert payload["code"] in {code.value for code in CredentialPreflightCode}


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_timeout_must_be_positive_and_finite(timeout: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds"):
        probe_native_store(
            backend=Keyring(),
            token_factory=fixed_secret("unused"),
            timeout_seconds=timeout,
        )


def test_child_output_redirection_failure_prevents_backend_access(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = tmp_path / "backend-accessed"
    monkeypatch.setattr(
        "officina.install.credential_preflight._discard_process_output",
        lambda: False,
    )

    result = probe_native_store(
        backend=Keyring(marker=marker),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=2,
    )

    assert result.code is CredentialPreflightCode.BACKEND_UNAVAILABLE
    assert not marker.exists()


@pytest.mark.parametrize(
    "message",
    [
        b"not-json",
        b"\xff",
        b'{"schema_version":1,"ok":true,"code":null,"backend":"unknown"}x',
        b'{"schema_version":1,"schema_version":1,"ok":true,"code":null,"backend":"unknown"}',
        b'{"schema_version":1,"ok":true,"code":null,"backend":"unknown","extra":1}',
        b'{"schema_version":true,"ok":true,"code":null,"backend":"unknown"}',
        b'{"schema_version":1,"ok":1,"code":null,"backend":"unknown"}',
        b'{"schema_version":1,"ok":false,"code":"invented","backend":"unknown"}',
        b'{"schema_version":1,"ok":true,"code":null,"backend":"backend controlled text"}',
    ],
)
def test_child_json_decoder_rejects_malformed_or_open_messages(message: bytes) -> None:
    outcome = preflight._decode_child_message(message, action="probe")

    assert outcome.abnormal
    assert outcome.result is None


def test_child_json_decoder_never_unpickles_payload(tmp_path: Path) -> None:
    marker = tmp_path / "pickle-executed"

    class MaliciousPickle:
        def __reduce__(self):
            return (Path.write_text, (marker, "executed"))

    outcome = preflight._decode_child_message(pickle.dumps(MaliciousPickle()), action="probe")

    assert outcome.abnormal
    assert not marker.exists()


def test_child_message_receive_is_size_bounded() -> None:
    limits: list[int | None] = []

    class OversizedConnection:
        def recv_bytes(self, maxlength: int | None = None) -> bytes:
            limits.append(maxlength)
            raise OSError("bad message length")

    outcome = preflight._receive_child_message(OversizedConnection(), action="probe")

    assert outcome.abnormal
    assert limits == [preflight._MAX_CHILD_MESSAGE_BYTES]


def test_child_message_encoder_emits_only_utf8_json_bytes() -> None:
    payload = {
        "schema_version": 1,
        "ok": False,
        "code": "backend_locked",
        "backend": _NATIVE_BACKEND_IDENTITY,
    }

    message = preflight._encode_child_message(payload)

    assert isinstance(message, bytes)
    assert json.loads(message.decode("utf-8")) == payload
    assert len(message) <= preflight._MAX_CHILD_MESSAGE_BYTES


def test_abnormal_collision_check_runs_separate_cleanup_child(tmp_path: Path) -> None:
    marker = tmp_path / "credential-present"
    pid_log = tmp_path / "pids.log"

    result = probe_native_store(
        backend=Keyring(mode="kill-during-check", marker=marker, pid_log=pid_log),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=2,
    )

    assert result.code is CredentialPreflightCode.BACKEND_UNAVAILABLE
    assert not marker.exists()
    pids = {
        int(line.split(":", 1)[1])
        for line in pid_log.read_text(encoding="utf-8").splitlines()
    }
    assert len(pids) >= 2


# famulus-skip: category=platform-contract; reason=requires POSIX process-group signals; alternate=production spawn and simulated native Job tests cover the non-POSIX path
@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group containment regression")
def test_timeout_terminates_backend_descendant_process_tree(tmp_path: Path) -> None:
    marker = tmp_path / "credential-present"
    descendant_pid_path = tmp_path / "descendant.pid"

    result = probe_native_store(
        backend=Keyring(
            mode="descendant-hang",
            marker=marker,
            descendant_pid=descendant_pid_path,
        ),
        token_factory=fixed_secret("probe-secret"),
        timeout_seconds=0.3,
    )

    assert result.code is CredentialPreflightCode.ROUNDTRIP_FAILED
    assert not marker.exists()
    descendant_pid = int(descendant_pid_path.read_text(encoding="utf-8"))
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        try:
            os.kill(descendant_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    else:
        os.kill(descendant_pid, signal.SIGKILL)
        pytest.fail("backend descendant survived the bounded probe timeout")


def _write_rejected_backend_module(tmp_path: Path) -> tuple[Path, Path, Path]:
    module_root = tmp_path / "selection-module"
    module_root.mkdir()
    access_log = tmp_path / "backend-access.log"
    selection_log = tmp_path / "backend-selection.log"
    (module_root / "evil_backend.py").write_text(
        "from keyring.backend import KeyringBackend\n"
        "from pathlib import Path\n"
        "import os\n"
        "def _event(value):\n"
        "    with Path(os.environ['FAMULUS_TEST_SELECTION_LOG']).open('a', encoding='utf-8') as stream:\n"
        "        stream.write(value + '\\n')\n"
        "_event('import:evil_backend')\n"
        "class EvilKeyring(KeyringBackend):\n"
        "    priority = 1000000\n"
        "    def __init__(self):\n"
        "        _event('initialize:evil_backend.EvilKeyring')\n"
        "    def _touch(self, operation):\n"
        "        Path(os.environ['FAMULUS_TEST_ACCESS_LOG']).write_text(operation)\n"
        "    def get_password(self, service, username):\n"
        "        self._touch('get')\n"
        "    def set_password(self, service, username, password):\n"
        "        self._touch('set')\n"
        "    def delete_password(self, service, username):\n"
        "        self._touch('delete')\n"
        "def initialize():\n"
        "    _event('entry-point:evil')\n",
        encoding="utf-8",
    )
    source_root = Path(__file__).parents[1] / "src"
    (module_root / "run_selection.py").write_text(
        textwrap.dedent(
            f"""
            import json
            import keyring
            import os
            import sys
            from importlib import metadata
            from pathlib import Path
            sys.path.insert(0, {str(source_root)!r})
            from officina.install.credential_preflight import probe_native_store

            selected = keyring.get_keyring()
            selected_identity = f"{{type(selected).__module__}}.{{type(selected).__name__}}"
            with Path(os.environ["FAMULUS_TEST_SELECTION_LOG"]).open(
                "a", encoding="utf-8"
            ) as stream:
                stream.write("selected:" + selected_identity + "\\n")

            if __name__ == "__main__":
                result = probe_native_store(timeout_seconds=10)
                print(json.dumps({{
                    "keyring_version": metadata.version("keyring"),
                    "selected_identity": selected_identity,
                    "result": result.as_json(),
                }}, separators=(",", ":"), sort_keys=True))
            """
        ),
        encoding="utf-8",
    )
    return module_root, access_log, selection_log


@pytest.mark.parametrize("selection_route", ["environment", "config", "entry-point"])
def test_real_keyring_selection_routes_are_rejected_before_backend_access(
    tmp_path: Path,
    selection_route: str,
) -> None:
    module_root, access_log, selection_log = _write_rejected_backend_module(tmp_path)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(module_root), str(Path(__file__).parents[1] / "src")]
    )
    environment["FAMULUS_TEST_ACCESS_LOG"] = str(access_log)
    environment["FAMULUS_TEST_SELECTION_LOG"] = str(selection_log)
    environment.pop("PYTHON_KEYRING_BACKEND", None)
    environment["XDG_CONFIG_HOME"] = str(tmp_path / "config")
    if selection_route == "environment":
        environment["PYTHON_KEYRING_BACKEND"] = "evil_backend.EvilKeyring"
    elif selection_route == "config":
        config = tmp_path / "config" / "python_keyring" / "keyringrc.cfg"
        config.parent.mkdir(parents=True)
        config.write_text(
            "[backend]\n"
            "default-keyring=evil_backend.EvilKeyring\n"
            f"keyring-path={module_root}\n",
            encoding="utf-8",
        )
    else:
        dist_info = module_root / "evil_backend-1.0.dist-info"
        dist_info.mkdir()
        (dist_info / "METADATA").write_text(
            "Metadata-Version: 2.1\nName: evil-backend\nVersion: 1.0\n",
            encoding="utf-8",
        )
        (dist_info / "entry_points.txt").write_text(
            "[keyring.backends]\nevil=evil_backend:initialize\n",
            encoding="utf-8",
        )

    completed = subprocess.run(
        [sys.executable, str(module_root / "run_selection.py")],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    )
    payload = json.loads(completed.stdout)
    events = selection_log.read_text(encoding="utf-8").splitlines()

    assert payload["selected_identity"] == "evil_backend.EvilKeyring"
    assert payload["keyring_version"] == "25.6.0"
    assert payload["result"]["code"] == "unsupported_backend"
    assert events.count("import:evil_backend") >= 2
    assert events.count("initialize:evil_backend.EvilKeyring") >= 2
    assert events.count("selected:evil_backend.EvilKeyring") >= 2
    if selection_route == "entry-point":
        assert events.count("entry-point:evil") >= 2
    else:
        assert "entry-point:evil" not in events
    assert not access_log.exists()


def _write_spawn_probe_fixture(tmp_path: Path, secret: str) -> tuple[Path, Path]:
    module_root = tmp_path / "spawn-fixture"
    module_root.mkdir()
    state_path = tmp_path / "credential-state"
    (module_root / "spawn_backend.py").write_text(
        textwrap.dedent(
            """
            import base64
            import json
            import os
            import sys
            import time
            from pathlib import Path
            from keyring.backend import KeyringBackend

            class KeyringLocked(Exception):
                pass

            class Keyring(KeyringBackend):
                priority = 100

                @property
                def state(self):
                    return Path(os.environ["FAMULUS_TEST_STATE"])

                @property
                def mode(self):
                    return os.environ["FAMULUS_TEST_MODE"]

                def get_password(self, service, username):
                    with open(os.environ["FAMULUS_TEST_CALLS"], "a", encoding="utf-8") as stream:
                        stream.write("get\\n")
                    if self.mode == "locked":
                        raise KeyringLocked("backend-controlled locked text")
                    return self.state.read_text(encoding="utf-8") if self.state.exists() else None

                def set_password(self, service, username, password):
                    with open(os.environ["FAMULUS_TEST_CALLS"], "a", encoding="utf-8") as stream:
                        stream.write("set\\n")
                    self.state.write_text(password, encoding="utf-8")
                    if self.mode == "timeout":
                        time.sleep(60)
                    if self.mode == "abnormal":
                        os._exit(23)
                    if self.mode == "malicious":
                        encoded = base64.b64encode(password.encode("utf-8")).decode("ascii")
                        print(password)
                        print(encoded, file=sys.stderr)
                        print(password.encode("utf-8").hex())
                        print(json.dumps(password)[1:-1], file=sys.stderr)
                        print("-----BEGIN " + "PRIVATE KEY-----" + encoded + "-----END " + "PRIVATE KEY-----")
                        raise RuntimeError("backend-controlled " + password)

                def delete_password(self, service, username):
                    with open(os.environ["FAMULUS_TEST_CALLS"], "a", encoding="utf-8") as stream:
                        stream.write("delete\\n")
                    if self.mode == "retain":
                        return
                    self.state.unlink(missing_ok=True)
            """
        ),
        encoding="utf-8",
    )
    source_root = Path(__file__).parents[1] / "src"
    (module_root / "run_probe.py").write_text(
        textwrap.dedent(
            f"""
            import json
            import os
            import sys
            sys.path.insert(0, {str(source_root)!r})
            sys.path.insert(0, {str(module_root)!r})
            from officina.common import secret_store
            from officina.install import credential_preflight as preflight

            IDENTITY = "spawn_backend.Keyring"
            secret_store.NATIVE_BACKENDS[sys.platform] = {{IDENTITY}}
            preflight._VALID_BACKEND_IDENTITIES = frozenset({{IDENTITY}})
            secret_store.metadata.version = lambda _name: secret_store.PINNED_KEYRING_VERSION

            def fixed_secret():
                return {secret!r}

            if __name__ == "__main__":
                result = preflight.probe_native_store(
                    token_factory=fixed_secret,
                    timeout_seconds=float(os.environ.get("FAMULUS_TEST_TIMEOUT", "2")),
                )
                print(json.dumps(result.as_json(), separators=(",", ":"), sort_keys=True))
            """
        ),
        encoding="utf-8",
    )
    return module_root / "run_probe.py", state_path


@pytest.mark.parametrize(
    ("mode", "expected_ok", "expected_code"),
    [
        ("normal", True, None),
        ("locked", False, "backend_locked"),
        ("timeout", False, "roundtrip_failed"),
        ("abnormal", False, "roundtrip_failed"),
        ("retain", False, "cleanup_failed"),
        ("malicious", False, "roundtrip_failed"),
    ],
)
def test_production_spawn_protocol_classification_and_cleanup(
    tmp_path: Path,
    mode: str,
    expected_ok: bool,
    expected_code: str | None,
) -> None:
    secret = 'SPAWN-CANARY-"\\' + "b" * 48
    runner, state_path = _write_spawn_probe_fixture(tmp_path, secret)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHON_KEYRING_BACKEND": "spawn_backend.Keyring",
            "FAMULUS_TEST_MODE": mode,
            "FAMULUS_TEST_STATE": str(state_path),
            "FAMULUS_TEST_CALLS": str(tmp_path / "backend-calls.log"),
            "FAMULUS_TEST_TIMEOUT": "1",
        }
    )

    completed = subprocess.run(
        [sys.executable, str(runner)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=8,
        check=True,
    )

    payload = json.loads(completed.stdout)
    assert payload["ok"] is expected_ok
    assert payload["code"] == expected_code
    if mode != "retain":
        assert not state_path.exists()
    else:
        assert state_path.exists()
        state_path.unlink()
    for canary in encoded_canaries(secret):
        assert canary not in completed.stdout + completed.stderr


def test_production_process_context_is_spawn() -> None:
    assert preflight._process_context(None).get_start_method() == "spawn"


def test_windows_job_containment_control_path(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeProcess:
        pid = 123

    monkeypatch.setattr(containment.sys, "platform", "win32")
    monkeypatch.setattr(
        containment,
        "_windows_create_kill_on_close_job",
        lambda pid: calls.append(("create", pid)) or 456,
    )
    monkeypatch.setattr(
        containment,
        "_windows_terminate_and_verify_job",
        lambda job, process: calls.append(("verify", job)) or process.pid == 123,
    )

    authority = containment._prepare_parent_containment(FakeProcess())

    assert authority == containment._ProcessContainment(pid=123, windows_job=456)
    assert containment._terminate_and_verify_tree(authority, FakeProcess())
    assert calls == [("create", 123), ("verify", 456)]


def test_windows_job_native_api_is_configured_and_assigned_before_go(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    calls: list[tuple[str, tuple[object, ...]]] = []

    class Function:
        def __init__(self, name: str, result: int) -> None:
            self.name = name
            self.result = result
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            calls.append((self.name, args))
            return self.result

    kernel32 = types.SimpleNamespace(
        CreateJobObjectW=Function("create", 501),
        SetInformationJobObject=Function("configure", 1),
        OpenProcess=Function("open", 502),
        AssignProcessToJobObject=Function("assign", 1),
        CloseHandle=Function("close", 1),
    )
    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    assert preflight._windows_create_kill_on_close_job(123) == 501
    assert [name for name, _args in calls] == [
        "create",
        "configure",
        "open",
        "assign",
        "close",
    ]


def test_windows_job_termination_is_bounded_and_verifies_empty_job(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import ctypes

    calls: list[str] = []

    class Function:
        def __init__(self, name: str, implementation) -> None:
            self.name = name
            self.implementation = implementation
            self.argtypes = None
            self.restype = None

        def __call__(self, *args):
            calls.append(self.name)
            return self.implementation(*args)

    query_count = 0

    def query(_job, _kind, info_pointer, _size, _returned) -> int:
        nonlocal query_count
        query_count += 1
        info_pointer._obj.ActiveProcesses = 1 if query_count == 1 else 0
        return 1

    kernel32 = types.SimpleNamespace(
        TerminateJobObject=Function("terminate", lambda *_args: 1),
        QueryInformationJobObject=Function("query", query),
        CloseHandle=Function("close", lambda *_args: 1),
    )

    class FakeProcess:
        def join(self, _timeout: float | None = None) -> None:
            return None

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(preflight.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda *_args, **_kwargs: kernel32, raising=False)

    assert preflight._windows_terminate_and_verify_job(501, FakeProcess())
    assert calls == ["query", "terminate", "query", "close"]


class _RetainedBackend:
    """In-memory backend used to observe retained worker state without secrets."""

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[str, int]] = []

    def store(self, namespace: str, key: str, secret: str) -> None:
        self.calls.append(("store", id(self)))
        self.values[(namespace, key)] = secret

    def lookup(self, namespace: str, key: str) -> str | None:
        self.calls.append(("lookup", id(self)))
        return self.values.get((namespace, key))

    def clear(self, namespace: str, key: str) -> bool:
        self.calls.append(("clear", id(self)))
        return self.values.pop((namespace, key), None) is not None


def _worker_request(command: str, **payload: object) -> dict[str, object]:
    return {
        "protocol_version": 1,
        "request_id": "request-1",
        "command": command,
        "payload": payload,
    }


def test_worker_state_retains_exact_preflight_backend_for_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Break caught: verification reselects a backend after preflight."""
    backend = _RetainedBackend()
    selected: list[_RetainedBackend] = []
    verified: list[int] = []
    key_id = "sha256:" + "a" * 64

    def factory() -> _RetainedBackend:
        selected.append(backend)
        return backend

    monkeypatch.setattr(
        preflight.secret_store,
        "native_backend_identity",
        lambda candidate: _NATIVE_BACKEND_IDENTITY
        if candidate is backend
        else pytest.fail("backend substituted"),
    )
    monkeypatch.setattr(
        preflight.certificate_records,
        "load_certificate_signing_key",
        lambda _root, *, secret_backend, allow_non_atomic=False: (
            verified.append(id(secret_backend))
            or types.SimpleNamespace(key_id=key_id)
        ),
    )

    state = preflight._ManagedCredentialWorkerState(
        backend_factory=factory,
        token_factory=lambda: "probe-secret",
    )
    assert state.dispatch(
        _worker_request("preflight", target_id=_TARGET_ONE)
    )["ok"] is True
    monkeypatch.setattr(
        preflight.certificate_records,
        "certificate_state_paths",
        lambda *, platform, home: types.SimpleNamespace(public_key_root=tmp_path),
    )
    response = state.dispatch(
        _worker_request("verify_certificate", platform="linux", home=str(tmp_path))
    )

    assert response == {
        "protocol_version": 1,
        "request_id": "request-1",
        "ok": True,
        "code": None,
        "result": {"verified": True, "key_id": key_id},
    }
    assert selected == [backend]
    assert {identity for _operation, identity in backend.calls} == {id(backend)}
    assert verified == [id(backend)]


def test_worker_state_rejects_order_command_and_schema_before_backend_or_filesystem(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: invalid protocol reaches backend or certificate APIs."""
    backend = _RetainedBackend()
    touched: list[str] = []
    monkeypatch.setattr(
        preflight.certificate_records,
        "load_certificate_signing_key",
        lambda *_a, **_k: touched.append("filesystem"),
    )
    invalid = [
        _worker_request("verify_certificate", platform="linux", home="/unused"),
        _worker_request("invented"),
        {**_worker_request("preflight", target_id=_TARGET_ONE), "extra": True},
        {**_worker_request("preflight", target_id=_TARGET_ONE), "protocol_version": True},
        _worker_request("preflight", target_id=_TARGET_ONE, extra=True),
    ]
    for request in invalid:
        state = preflight._ManagedCredentialWorkerState(
            backend_factory=lambda: backend,
            token_factory=lambda: "probe-secret",
        )
        response = state.dispatch(request)
        assert response["ok"] is False
        assert response["code"] in {"invalid_state", "invalid_request"}
        assert state.dispatch(
            _worker_request("preflight", target_id=_TARGET_ONE)
        )["code"] == "invalid_state"
    assert backend.calls == []
    assert touched == []


def test_worker_state_preflight_failure_blocks_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _RetainedBackend()
    monkeypatch.setattr(
        preflight.secret_store,
        "native_backend_identity",
        lambda _backend: (_ for _ in ()).throw(
            preflight.secret_store.SecretStoreUnsupportedBackend("secret detail")
        ),
    )
    state = preflight._ManagedCredentialWorkerState(
        backend_factory=lambda: backend,
        token_factory=lambda: "probe-secret",
    )

    failed = state.dispatch(
        _worker_request("preflight", target_id=_TARGET_ONE)
    )
    blocked = state.dispatch(
        _worker_request("verify_certificate", platform="linux", home="/unused")
    )

    assert failed["code"] == "unsupported_backend"
    assert blocked["code"] == "invalid_state"
    assert "secret detail" not in repr((failed, blocked))


@pytest.mark.parametrize(
    "message",
    [
        b"not-json",
        b"\xff",
        b'{"protocol_version":1,"protocol_version":1}',
        b'{"protocol_version":1} trailing',
        pickle.dumps({"command": "preflight"}),
        b'{"protocol_version":NaN}',
    ],
)
def test_worker_request_decoder_rejects_noncanonical_payloads(message: bytes) -> None:
    """Break caught: hostile bytes are deserialized or dispatched."""
    with pytest.raises(CredentialWorkerError) as caught:
        preflight._decode_worker_request(message)
    assert caught.value.code is CredentialWorkerCode.INVALID_REQUEST


def test_worker_request_decoder_is_size_bounded() -> None:
    with pytest.raises(CredentialWorkerError) as caught:
        preflight._decode_worker_request(
            b"{" + b"x" * preflight._MAX_WORKER_MESSAGE_BYTES
        )
    assert caught.value.code is CredentialWorkerCode.INVALID_REQUEST


def test_managed_worker_requires_explicit_executable_and_finite_deadlines() -> None:
    """Break caught: ambient interpreter fallback or unbounded worker lifetime."""
    with pytest.raises(TypeError, match="managed_python"):
        ManagedCredentialWorker(None)  # type: ignore[arg-type]
    for executable in ("python", "bin/python", "/managed/../python", "bad\x00path"):
        with pytest.raises(ValueError, match="managed_python"):
            ManagedCredentialWorker(executable)
    for value in (False, True, 0, -1, float("inf"), float("nan")):
        with pytest.raises(ValueError):
            ManagedCredentialWorker("/managed/python", command_timeout_seconds=value)
        with pytest.raises(ValueError):
            ManagedCredentialWorker("/managed/python", total_timeout_seconds=value)


def test_managed_worker_spawns_shell_free_isolated_module_with_anonymous_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: production launch falls back to ambient Python or stdio IPC."""
    recorded: dict[str, object] = {}

    class Process:
        pid = 42
        returncode = None

    def spawn(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(preflight.subprocess, "Popen", spawn)
    monkeypatch.setattr(preflight, "_prepare_subprocess_containment", lambda _p: object())
    monkeypatch.setattr(
        preflight,
        "_terminate_and_verify_subprocess_tree",
        lambda _containment, _process: True,
    )
    monkeypatch.setattr(preflight, "_await_worker_ready", lambda *_a, **_k: None)
    worker = ManagedCredentialWorker(
        "/managed/python",
        command_timeout_seconds=1,
        total_timeout_seconds=5,
    )
    worker.start()

    inherited = tuple(int(value) for value in recorded["argv"][-2:])
    assert recorded["argv"][:-2] == [
        "/managed/python",
        "-I",
        "-m",
        "officina.install.credential_preflight",
        "--managed-worker-fds",
    ]
    assert recorded["kwargs"]["shell"] is False
    assert recorded["kwargs"]["stdin"] is preflight.subprocess.DEVNULL
    assert recorded["kwargs"]["stdout"] is preflight.subprocess.DEVNULL
    assert recorded["kwargs"]["stderr"] is preflight.subprocess.DEVNULL
    assert recorded["kwargs"]["pass_fds"] == inherited
    worker._force_cleanup()


def test_windows_worker_launch_uses_explicit_inherited_handle_not_posix_pass_fds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: Windows launch receives a POSIX-only pass_fds contract."""
    recorded: dict[str, object] = {}

    class Process:
        pid = 42
        returncode = None

    def spawn(argv, **kwargs):
        recorded["argv"] = argv
        recorded["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(preflight.os, "name", "nt")
    monkeypatch.setattr(preflight.subprocess, "Popen", spawn)
    monkeypatch.setattr(
        preflight,
        "_windows_prepare_pipe_handles",
        lambda _fds: ((9876, 9877), types.SimpleNamespace(lpAttributeList={"handle_list": [9876, 9877]})),
    )
    monkeypatch.setattr(preflight, "_prepare_subprocess_containment", lambda _p: object())
    monkeypatch.setattr(preflight, "_await_worker_ready", lambda *_a, **_k: None)
    monkeypatch.setattr(
        preflight,
        "_terminate_and_verify_subprocess_tree",
        lambda _containment, _process: True,
    )
    worker = ManagedCredentialWorker(
        "C:/managed/python.exe",
        command_timeout_seconds=1,
        total_timeout_seconds=5,
    )
    worker.start()

    assert recorded["argv"][-3:] == ["--managed-worker-handles", "9876", "9877"]
    assert "pass_fds" not in recorded["kwargs"]
    assert recorded["kwargs"]["close_fds"] is True
    assert recorded["kwargs"]["startupinfo"].lpAttributeList == {
        "handle_list": [9876, 9877]
    }
    worker._force_cleanup()


def test_managed_worker_verification_result_is_closed_and_secret_free() -> None:
    result = CredentialVerificationResult(
        verified=True,
        key_id="sha256:" + "d" * 64,
    )
    assert result.as_json() == {"verified": True, "key_id": "sha256:" + "d" * 64}


def test_managed_worker_parent_owns_target_and_retries_collision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: a stored probe target is unknown to the parent after child death."""
    worker = ManagedCredentialWorker(
        "/managed/python",
        command_timeout_seconds=1,
        total_timeout_seconds=5,
    )
    worker._process = object()  # type: ignore[assignment]
    worker._channel = object()  # type: ignore[assignment]
    worker._deadline = time.monotonic() + 5
    targets = iter([_TARGET_ONE, _TARGET_TWO])
    seen: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(preflight, "_new_target_id", lambda: next(targets))

    def request(
        command: str,
        payload: dict[str, object],
        **_kwargs: object,
    ) -> dict[str, object]:
        seen.append((command, payload))
        if payload["target_id"] == _TARGET_ONE:
            raise CredentialWorkerError(CredentialWorkerCode.TARGET_COLLISION)
        return {
            "schema_version": 1,
            "ok": True,
            "code": None,
            "backend": _NATIVE_BACKEND_IDENTITY,
        }

    monkeypatch.setattr(worker, "_request", request)

    assert worker.preflight().ok
    assert seen == [
        ("preflight", {"target_id": _TARGET_ONE}),
        ("preflight", {"target_id": _TARGET_TWO}),
    ]


def test_worker_state_collision_is_nonterminal_and_second_target_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: collision consumes worker state and makes retry impossible."""
    backend = _RetainedBackend()
    backend.values[(preflight._PROBE_NAMESPACE, _TARGET_ONE)] = "occupied"
    selected: list[int] = []
    monkeypatch.setattr(
        preflight.secret_store,
        "native_backend_identity",
        lambda _backend: _NATIVE_BACKEND_IDENTITY,
    )
    state = preflight._ManagedCredentialWorkerState(
        backend_factory=lambda: (selected.append(id(backend)) or backend),
        token_factory=lambda: "probe-secret",
    )

    collision = state.dispatch(
        _worker_request("preflight", target_id=_TARGET_ONE)
    )
    succeeded = state.dispatch(
        _worker_request("preflight", target_id=_TARGET_TWO)
    )

    assert collision["code"] == "target_collision"
    assert succeeded["ok"] is True
    assert backend.values == {
        (preflight._PROBE_NAMESPACE, _TARGET_ONE): "occupied"
    }
    assert selected == [id(backend)]


@pytest.mark.parametrize(
    "target_id",
    [
        "native-preflight-short",
        "native-preflight-" + "A" * 32,
        "native-preflight-" + "g" * 32,
        "other-" + "1" * 32,
        "native-preflight-" + "1" * 32 + ":other:key",
    ],
)
def test_cleanup_target_schema_rejects_namespace_or_key_injection_before_access(
    monkeypatch: pytest.MonkeyPatch,
    target_id: str,
) -> None:
    touched: list[str] = []
    monkeypatch.setattr(
        preflight.secret_store,
        "KeyringSecretBackend",
        lambda: touched.append("backend"),
    )

    assert not preflight._valid_target_id(target_id)
    assert not preflight._run_managed_cleanup(
        "/managed/python", target_id, time.monotonic() + 1
    )
    assert touched == []


def test_managed_worker_abnormal_preflight_runs_exact_target_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: worker dies after store and leaves an unenumerable probe secret."""
    worker = ManagedCredentialWorker(
        "/managed/python",
        command_timeout_seconds=1,
        total_timeout_seconds=5,
    )
    worker._process = object()  # type: ignore[assignment]
    worker._channel = object()  # type: ignore[assignment]
    worker._deadline = time.monotonic() + 5
    monkeypatch.setattr(preflight, "_new_target_id", lambda: _TARGET_ONE)
    monkeypatch.setattr(
        worker,
        "_request",
        lambda *_a, **_k: (_ for _ in ()).throw(
            CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
        ),
    )
    cleanup: list[tuple[str, str, float]] = []
    monkeypatch.setattr(
        preflight,
        "_run_managed_cleanup",
        lambda executable, target_id, deadline: (
            cleanup.append((executable, target_id, deadline)) or True
        ),
    )

    with pytest.raises(CredentialWorkerError) as caught:
        worker.preflight()

    assert caught.value.code is CredentialWorkerCode.WORKER_FAILED
    assert cleanup[0][:2] == ("/managed/python", _TARGET_ONE)
    assert cleanup[0][2] <= worker._deadline


def test_managed_worker_unproven_emergency_cleanup_is_cleanup_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = ManagedCredentialWorker(
        "/managed/python",
        command_timeout_seconds=1,
        total_timeout_seconds=5,
    )
    worker._process = object()  # type: ignore[assignment]
    worker._channel = object()  # type: ignore[assignment]
    worker._deadline = time.monotonic() + 5
    monkeypatch.setattr(preflight, "_new_target_id", lambda: _TARGET_ONE)
    monkeypatch.setattr(
        worker,
        "_request",
        lambda *_a, **_k: (_ for _ in ()).throw(
            CredentialWorkerError(CredentialWorkerCode.WORKER_FAILED)
        ),
    )
    monkeypatch.setattr(preflight, "_run_managed_cleanup", lambda *_a: False)

    with pytest.raises(CredentialWorkerError) as caught:
        worker.preflight()
    assert caught.value.code is CredentialWorkerCode.CLEANUP_FAILED


def test_managed_worker_preserves_cleanup_subdeadline_after_request_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Break caught: main-worker termination consumes the emergency cleanup budget."""
    worker = ManagedCredentialWorker(
        "/managed/python",
        command_timeout_seconds=1,
        total_timeout_seconds=5,
    )
    worker._process = object()  # type: ignore[assignment]
    worker._channel = object()  # type: ignore[assignment]
    worker._deadline = time.monotonic() + 5
    monkeypatch.setattr(preflight, "_new_target_id", lambda: _TARGET_ONE)
    monkeypatch.setattr(
        worker,
        "_request",
        lambda *_a, **_k: (_ for _ in ()).throw(
            CredentialWorkerError(CredentialWorkerCode.TIMEOUT)
        ),
    )
    seen: list[float] = []
    monkeypatch.setattr(
        preflight,
        "_run_managed_cleanup",
        lambda _executable, _target, deadline: (
            seen.append(deadline - time.monotonic()) or True
        ),
    )

    with pytest.raises(CredentialWorkerError) as caught:
        worker.preflight()

    assert caught.value.code is CredentialWorkerCode.TIMEOUT
    assert seen and seen[0] >= preflight._EMERGENCY_CLEANUP_SECONDS * 0.9


def test_channel_deadline_expiry_maps_to_timeout() -> None:
    class TimedOutSocket:
        def settimeout(self, _timeout: float) -> None:
            pass

        def recv(self, _length: int) -> bytes:
            raise TimeoutError

    with pytest.raises(CredentialWorkerError) as caught:
        preflight._recv_exact(TimedOutSocket(), 1, time.monotonic() + 1)
    assert caught.value.code is CredentialWorkerCode.TIMEOUT


def test_anonymous_channel_rejects_unbounded_read_and_write() -> None:
    channel, child_fds = preflight._anonymous_duplex_pair()
    try:
        with pytest.raises(RuntimeError, match="timeout"):
            channel.recv(1)
        with pytest.raises(RuntimeError, match="timeout"):
            channel.sendall(b"x")
    finally:
        channel.close()
        preflight._close_descriptors(child_fds)


def test_frame_reader_accepts_partial_header_and_body() -> None:
    class PartialChannel:
        def __init__(self) -> None:
            self.parts = [b"\x00", b"\x00\x00", b"\x03", b"a", b"bc"]

        def settimeout(self, _timeout: float) -> None:
            pass

        def recv(self, _length: int) -> bytes:
            return self.parts.pop(0)

    assert preflight._recv_frame(PartialChannel(), time.monotonic() + 1) == b"abc"


@pytest.mark.parametrize("header", [b"\x00\x00\x00\x00", b"\x00\x00\x10\x01"])
def test_frame_reader_rejects_zero_or_oversized_length_before_body_read(
    header: bytes,
) -> None:
    class HeaderChannel:
        def __init__(self) -> None:
            self.calls = 0

        def settimeout(self, _timeout: float) -> None:
            pass

        def recv(self, _length: int) -> bytes:
            self.calls += 1
            return header

    channel = HeaderChannel()
    with pytest.raises(CredentialWorkerError):
        preflight._recv_frame(channel, time.monotonic() + 1)
    assert channel.calls == 1


def test_frame_reader_eof_is_terminal() -> None:
    class EofChannel:
        def settimeout(self, _timeout: float) -> None:
            pass

        def recv(self, _length: int) -> bytes:
            return b""

    with pytest.raises(CredentialWorkerError) as caught:
        preflight._recv_frame(EofChannel(), time.monotonic() + 1)
    assert caught.value.code is CredentialWorkerCode.WORKER_FAILED


def test_frame_writer_backpressure_deadline_maps_to_timeout() -> None:
    class BackpressuredChannel:
        def settimeout(self, _timeout: float) -> None:
            pass

        def sendall(self, _value: bytes) -> None:
            raise TimeoutError

    with pytest.raises(CredentialWorkerError) as caught:
        preflight._send_frame(
            BackpressuredChannel(), b"{}", time.monotonic() + 1
        )
    assert caught.value.code is CredentialWorkerCode.TIMEOUT


# famulus-skip: category=platform-contract; reason=fixture executable is a POSIX shell shim around the current isolated Python; alternate=Windows inheritable-handle launch and native Job containment branches are unit-tested separately
@pytest.mark.skipif(os.name == "nt", reason="POSIX controlled managed-Python fixture")
def test_real_managed_worker_retains_one_process_and_backend_then_is_absent(
    tmp_path: Path,
) -> None:
    """Break caught: transport respawns/reselects or returns before process absence."""
    source_root = Path(__file__).parents[1] / "src"
    events = tmp_path / "events.jsonl"
    managed_python = tmp_path / "managed-python"
    bootstrap = textwrap.dedent(
        f"""
        import json, os, sys, types
        os.setsid()
        sys.path.insert(0, {str(source_root)!r})
        import keyring
        import keyring.backends.SecretService as native
        state = {{}}
        events = {str(events)!r}
        def event(operation, backend):
            with open(events, "a", encoding="utf-8") as stream:
                stream.write(json.dumps({{"operation": operation, "pid": os.getpid(), "backend": id(backend)}}) + "\\n")
        def lookup(self, service, key):
            event("lookup", self); return state.get((service, key))
        def store(self, service, key, secret):
            event("store", self); state[(service, key)] = secret
        def clear(self, service, key):
            event("clear", self); state.pop((service, key), None)
        native.Keyring.get_password = lookup
        native.Keyring.set_password = store
        native.Keyring.delete_password = clear
        selected = object.__new__(native.Keyring)
        keyring.set_keyring(selected)
        from officina.common import certificate_records
        from officina.install import credential_preflight as worker_module
        certificate_records.load_certificate_signing_key = lambda root, *, secret_backend, allow_non_atomic=False: (event("verify", secret_backend) or types.SimpleNamespace(key_id="sha256:" + "e" * 64))
        worker_module._establish_child_containment = lambda: True
        worker_module._discard_process_output = lambda: True
        raise SystemExit(worker_module.main(sys.argv[4:]))
        """
    ).strip()
    managed_python.write_text(
        f"#!{sys.executable!s}\n" + bootstrap + "\n",
        encoding="utf-8",
    )
    managed_python.chmod(0o700)

    worker = ManagedCredentialWorker(
        str(managed_python),
        command_timeout_seconds=3,
        total_timeout_seconds=8,
    )
    worker.start()
    pid = worker._process.pid
    assert worker.preflight().ok
    assert worker.verify_certificate(platform="linux", home=tmp_path) == (
        CredentialVerificationResult(True, "sha256:" + "e" * 64)
    )
    assert worker._process.pid == pid
    worker.close()

    records = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert {record["pid"] for record in records} == {pid}
    native_records = [record for record in records if record["operation"] != "verify"]
    verify_records = [record for record in records if record["operation"] == "verify"]
    assert len({record["backend"] for record in native_records}) == 1
    assert len(verify_records) == 1
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
