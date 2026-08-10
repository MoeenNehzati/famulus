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
