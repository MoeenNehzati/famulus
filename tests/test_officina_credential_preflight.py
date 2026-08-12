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
