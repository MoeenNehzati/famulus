from __future__ import annotations

import argparse
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml

from officina.dispatcher import cli
from officina.dispatcher.errors import DirectBlueprintError


@pytest.fixture
def live_stream_repository(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create the smallest real dispatcher repository with a blocking child."""
    module_root = tmp_path / "skills" / "probe"
    blueprint_root = module_root / "blueprints"
    blueprint_root.mkdir(parents=True)
    (tmp_path / "officina.toml").write_text(
        'schema_version = 1\n[modules]\nroots = ["skills"]\n',
        encoding="utf-8",
    )
    (module_root / "__init__.py").write_text("", encoding="utf-8")
    (module_root / "blueprint.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "module",
                "id": "probe",
                "version": 1,
                "description": "Dispatcher stream probe.",
                "discovery": {"mechanism": "skill"},
                "gateway": {"path": "__init__.py", "language": "Python"},
                "content": [r"__init__\.py"],
                "authority": {"owns_filesystem": []},
                "sources": {
                    "probe.source.runtime": {
                        "blueprint": {
                            "base": "module-root",
                            "path": "blueprints/runtime.yaml",
                        }
                    }
                },
                "children": {},
                "namespace_exports": {},
                "exports": {
                    "probe.interface.stream": {
                        "source_interface": "probe.source.runtime.interface.stream",
                        "access": {"allow_all_modules": False, "allowed_callers": []},
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (blueprint_root / "runtime.yaml").write_text(
        yaml.safe_dump(
            {
                "schema_version": 6,
                "node_type": "behavioral_source",
                "id": "probe.source.runtime",
                "version": 1,
                "description": "Emit a diagnostic before a synchronization wait.",
                "gateway": {"path": "runtime.py", "language": "Python>=3.11"},
                "content": [r"runtime\.py"],
                "dependencies": [],
                "uses_interfaces": [],
                "interfaces": {
                    "probe.source.runtime.interface.stream": {
                        "version": 1,
                        "description": "Exercise inherited child streams.",
                        "contract": {"arguments": {}},
                        "process_binding": {
                            "kind": "process",
                            "entry": "StreamInterface",
                            "args_prefix": ["stream"],
                            "arguments": {},
                        },
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (module_root / "runtime.py").write_text(
        "import os\n"
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "from officina.runtime.python_machine_interface import PythonMachineInterface\n"
        "class StreamInterface(PythonMachineInterface):\n"
        "    def build_parser(self):\n"
        "        parser = super().build_parser()\n"
        "        parser.add_argument('command')\n"
        "        return parser\n"
        "    def run(self, args):\n"
        "        sys.stderr.write('oauth.authorization_url\\n')\n"
        "        sys.stderr.flush()\n"
        "        release = Path(os.environ['DISPATCHER_STREAM_RELEASE'])\n"
        "        while not release.exists():\n"
        "            time.sleep(0.01)\n"
        "        print('authorized')\n"
        "        return 0\n",
        encoding="utf-8",
    )
    return tmp_path / "officina.toml", tmp_path / "release", module_root


@pytest.fixture
def process_registry():
    processes: list[subprocess.Popen[str]] = []

    def start(*args, **kwargs) -> subprocess.Popen[str]:
        process = subprocess.Popen(*args, **kwargs)
        processes.append(process)
        return process

    yield start

    for process in processes:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)


@pytest.fixture
def cli_args() -> argparse.Namespace:
    return argparse.Namespace(
        caller_skill="connect-google",
        stdin=False,
        dry_run=False,
        error_format="text",
        target_or_skill="connect-google._rtx.interface.authorize-services",
        rest=["--services", "drive"],
        repository_config=None,
    )


def test_cli_inherits_child_streams_instead_of_buffering_and_replaying(
    monkeypatch: pytest.MonkeyPatch, cli_args: argparse.Namespace
) -> None:
    observed = {}

    def fake_dispatch(**kwargs):
        observed.update(kwargs)
        return subprocess.CompletedProcess(args=[], returncode=0, stdout=None, stderr=None)

    monkeypatch.setattr(cli, "parse_cli", lambda: cli_args)
    monkeypatch.setattr(cli, "_dispatch_host", fake_dispatch)

    assert cli.main() == 0
    assert observed["capture_output"] is False


def test_live_cli_exposes_child_stderr_before_child_completes(
    live_stream_repository: tuple[Path, Path, Path], process_registry
) -> None:
    config, release, _module_root = live_stream_repository
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    environment["DISPATCHER_STREAM_RELEASE"] = str(release)
    process = process_registry(
        [
            sys.executable,
            "-P",
            "-m",
            "officina.dispatcher.cli",
            "--repository-config",
            str(config),
            "--caller-skill",
            "probe",
            "probe.interface.stream",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    assert process.stderr is not None
    observed: queue.Queue[str] = queue.Queue()
    def read_two_lines() -> None:
        observed.put(process.stderr.readline())
        observed.put(process.stderr.readline())

    reader = threading.Thread(target=read_two_lines, daemon=True)
    reader.start()

    assert observed.get(timeout=3).startswith(
        "warning: certification-status-unavailable:"
    )
    assert observed.get(timeout=3) == "oauth.authorization_url\n"
    assert process.poll() is None
    release.touch()
    stdout, stderr = process.communicate(timeout=3)

    reader.join(timeout=1)
    assert stdout == "authorized\n"
    assert stderr == ""
    assert process.returncode == 0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("probe.interface.stream", ("probe.interface.stream", None)),
        ("probe.interface.stream@1", ("probe.interface.stream", 1)),
        ("probe.interface.stream@12", ("probe.interface.stream", 12)),
        # Not a usable pin: left intact so normal id validation rejects it.
        ("probe.interface.stream@0", ("probe.interface.stream@0", None)),
        ("probe.interface.stream@x", ("probe.interface.stream@x", None)),
        ("probe.interface.stream@", ("probe.interface.stream@", None)),
        ("probe.interface.stream@١", ("probe.interface.stream@١", None)),
    ],
)
def test_split_target_version(raw: str, expected: tuple[str, int | None]) -> None:
    assert cli._split_target_version(raw) == expected


def _record_dispatch(monkeypatch, cli_args, results):
    """Stub _dispatch_host, recording each target_version it is called with."""
    calls: list[int | None] = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs["target_version"])
        outcome = results[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(cli, "parse_cli", lambda: cli_args)
    monkeypatch.setattr(cli, "_dispatch_host", fake_dispatch)
    return calls


_OK = subprocess.CompletedProcess(args=[], returncode=0, stdout=None, stderr=None)


def test_cli_forwards_version_pin_and_strips_it_from_the_target(
    monkeypatch: pytest.MonkeyPatch, cli_args: argparse.Namespace
) -> None:
    cli_args.target_or_skill = "connect-google._rtx.interface.authorize-services@1"
    observed = {}

    def fake_dispatch(**kwargs):
        observed.update(kwargs)
        return _OK

    monkeypatch.setattr(cli, "parse_cli", lambda: cli_args)
    monkeypatch.setattr(cli, "_dispatch_host", fake_dispatch)

    assert cli.main() == 0
    assert observed["target"] == "connect-google._rtx.interface.authorize-services"
    assert observed["target_version"] == 1


def test_cli_leaves_version_unset_when_the_target_is_unpinned(
    monkeypatch: pytest.MonkeyPatch, cli_args: argparse.Namespace
) -> None:
    calls = _record_dispatch(monkeypatch, cli_args, [_OK])

    assert cli.main() == 0
    assert calls == [None]


def test_cli_warns_and_resolves_anyway_when_a_version_pin_is_stale(
    monkeypatch: pytest.MonkeyPatch,
    cli_args: argparse.Namespace,
    capsys: pytest.CaptureFixture[str],
) -> None:
    target = "connect-google._rtx.interface.authorize-services"
    cli_args.target_or_skill = f"{target}@1"
    stale = DirectBlueprintError(
        f"version mismatch for {target}: requested 1, available 2",
        code="dispatcher.interface_version_mismatch",
        target_module_id="connect-google._rtx",
    )
    calls = _record_dispatch(monkeypatch, cli_args, [stale, _OK])

    assert cli.main() == 0
    assert calls == [1, None]
    stderr = capsys.readouterr().err
    assert "warning: interface-version-pin-stale:" in stderr
    assert "requested 1, available 2" in stderr
    assert f"[{target}]" in stderr


def test_cli_forwards_identical_stdin_to_both_attempts_of_a_stale_pin(
    monkeypatch: pytest.MonkeyPatch,
    cli_args: argparse.Namespace,
    tmp_path: Path,
) -> None:
    """The retry must not re-read an already-drained stdin buffer."""
    target = "connect-google._rtx.interface.authorize-services"
    cli_args.target_or_skill = f"{target}@1"
    cli_args.stdin = True
    payload = b'{"services": ["drive"]}'
    source = tmp_path / "stdin"
    source.write_bytes(payload)

    observed: list[bytes | None] = []

    def fake_dispatch(**kwargs):
        observed.append(kwargs["stdin"])
        if len(observed) == 1:
            raise DirectBlueprintError(
                f"version mismatch for {target}: requested 1, available 2",
                code="dispatcher.interface_version_mismatch",
                target_module_id="connect-google._rtx",
            )
        return _OK

    with source.open("rb") as handle:
        monkeypatch.setattr(sys, "stdin", type("S", (), {"buffer": handle})())
        monkeypatch.setattr(cli, "parse_cli", lambda: cli_args)
        monkeypatch.setattr(cli, "_dispatch_host", fake_dispatch)
        assert cli.main() == 0

    assert observed == [payload, payload]


def test_live_cli_accepts_a_version_pin_and_warns_when_it_is_stale(
    live_stream_repository: tuple[Path, Path, Path],
) -> None:
    """End-to-end, through a real child process rather than a stub."""
    config, release, _module_root = live_stream_repository
    release.touch()  # let the child exit immediately
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    environment["DISPATCHER_STREAM_RELEASE"] = str(release)

    def run(target: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable, "-P", "-m", "officina.dispatcher.cli",
                "--repository-config", str(config),
                "--caller-skill", "probe",
                target,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            env=environment,
        )

    matching = run("probe.interface.stream@1")
    assert matching.returncode == 0, matching.stderr
    assert matching.stdout == "authorized\n"
    assert "interface-version-pin-stale" not in matching.stderr

    stale = run("probe.interface.stream@7")
    assert stale.returncode == 0, stale.stderr
    assert stale.stdout == "authorized\n"
    assert "warning: interface-version-pin-stale:" in stale.stderr
    assert "requested 7, available 1" in stale.stderr

    bare = run("probe.interface.stream")
    assert bare.returncode == 0, bare.stderr
    assert bare.stdout == "authorized\n"


def test_cli_does_not_retry_failures_unrelated_to_the_version_pin(
    monkeypatch: pytest.MonkeyPatch, cli_args: argparse.Namespace
) -> None:
    cli_args.target_or_skill = "connect-google._rtx.interface.authorize-services@1"
    missing = DirectBlueprintError(
        "interface not found",
        code="dispatcher.interface_not_found",
        target_module_id="connect-google._rtx",
    )
    calls = _record_dispatch(monkeypatch, cli_args, [missing])

    assert cli.main() == 2
    assert calls == [1]
