from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from dataclasses import dataclass, field

import pytest

from officina.blueprints.graph import load_repository_blueprint_graph


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "setup-python-environment" / "SKILL.md"


def _templates() -> dict[str, list[str]]:
    text = SKILL.read_text(encoding="utf-8")
    return {
        name: json.loads(payload)
        for name, payload in re.findall(
            r"<!-- command:([a-z-]+) -->\n```json\n([^`]+)```", text
        )
    }


@dataclass
class _SimulatedHost:
    scenario: str
    platform: str
    canonical: str = "/tmp/Selected Python/python"
    calls: list[list[str]] = field(default_factory=list)
    mutations: int = 0
    installed: bool = False
    fingerprint_calls: int = 0

    def run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[0] == "python":
            if self.scenario in {"missing", "python3-only", "py-only"}:
                raise FileNotFoundError("python")
            if len(argv) == 2 and argv[1].endswith("mcp_server.py"):
                return subprocess.CompletedProcess(argv, 0 if self.installed else 1, "", "")
            self.fingerprint_calls += 1
            version = [3, 10] if self.scenario == "old" else [3, 12]
            executable = (
                "/tmp/Other Python/python"
                if self.scenario == "drift" and self.fingerprint_calls == 2
                else self.canonical
            )
            if self.scenario == "regression" and self.fingerprint_calls == 2:
                version = [3, 10]
            payload = {
                "executable": executable,
                "prefix": "/tmp/Selected Python",
                "base_prefix": "/tmp/Selected Python",
                "version": version,
            }
            return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
        assert argv[0] == self.canonical
        if argv[1:4] == ["-m", "pip", "--version"]:
            code = 1 if self.scenario == "missing-pip" else 0
            return subprocess.CompletedProcess(argv, code, "pip 25", "pip unavailable")
        if argv[1] == "-c":
            code = 1 if self.scenario == "non-writable" else 0
            return subprocess.CompletedProcess(argv, code, "", "target is not writable")
        if "--dry-run" in argv:
            if self.scenario == "externally-managed":
                return subprocess.CompletedProcess(argv, 1, "", "externally managed")
            installs = [] if self.scenario == "satisfied" else [{"metadata": {"name": "mcp"}}]
            return subprocess.CompletedProcess(argv, 0, json.dumps({"install": installs}), "")
        self.mutations += 1
        self.installed = True
        return subprocess.CompletedProcess(
            argv, 0, json.dumps({"install": [{"metadata": {"name": "mcp"}}]}), ""
        )


def _expand(argv: list[str], canonical: str, packages: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in argv:
        if token == "${canonical_executable}":
            expanded.append(canonical)
        elif token == "${selected_packages}":
            expanded.extend(packages)
        else:
            expanded.append(token)
    return expanded


def _consume_setup(host: _SimulatedHost, plugin: Path) -> str:
    templates = _templates()
    packages = json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))[
        "core_packages"
    ]
    try:
        initial = host.run(templates["initial-fingerprint"])
    except FileNotFoundError:
        return "missing-python"
    if initial.returncode:
        return "fingerprint-failed"
    selected = json.loads(initial.stdout)
    if selected["version"] < [3, 11]:
        return "old-python"
    canonical = selected["executable"]
    for name in ("pip-check", "target-check"):
        result = host.run(_expand(templates[name], canonical, packages))
        if result.returncode:
            return name + "-failed"
    preflight = host.run(_expand(templates["pip-preflight"], canonical, packages))
    if preflight.returncode:
        return "pip-preflight-failed"
    if json.loads(preflight.stdout)["install"]:
        installed = host.run(_expand(templates["pip-install"], canonical, packages))
        if installed.returncode:
            return "pip-install-failed"
    final = host.run(templates["final-fingerprint"])
    if final.returncode:
        return "final-fingerprint-failed"
    observed = json.loads(final.stdout)
    if observed["version"] < [3, 11] or observed != selected:
        return "fingerprint-changed"
    server = host.run(["python", str(plugin / "mcp_server.py")])
    return "ready" if server.returncode == 0 else "mcp-start-failed"


def test_setup_skill_is_host_loaded_and_uses_task_1_core_authority() -> None:
    graph = load_repository_blueprint_graph(
        ROOT,
        schema_root=ROOT / "references" / "blueprint-schema",
        expected_schema_version=6,
    )
    core = json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))
    text = SKILL.read_text(encoding="utf-8")

    export = graph.exports["setup-python-environment.interface.setup"]
    assert export.source_interface_id == (
        "setup-python-environment.source.gateway.interface.default"
    )
    assert "tools:\n  - python" in text
    assert core["core_packages"] == ["mcp>=1,<2", "PyYAML>=6", "jsonschema>=4,<5"]
    assert "installation_tier" not in text
    assert all(term not in text.casefold() for term in ("keyring", "google"))
    assert graph.exports[
        "setup-python-environment.interface.repair-selected-packages"
    ].source_interface_id == export.source_interface_id


def test_graph_execution_contract_covers_the_actual_ordered_command_sequence() -> None:
    graph = load_repository_blueprint_graph(
        ROOT,
        schema_root=ROOT / "references" / "blueprint-schema",
        expected_schema_version=6,
    )
    contract = graph.nodes[
        "setup-python-environment.source.gateway"
    ].declaration["interfaces"][
        "setup-python-environment.source.gateway.interface.default"
    ]["contract"]
    subprocesses = {
        item["id"]: item
        for item in contract["direct_io"]["writes"]
        if item["medium"] == "subprocess"
    }

    assert subprocesses["literal-python"]["path"] == "python"
    assert subprocesses["literal-python"]["path_match"] == "exact"
    assert subprocesses["selected-python"]["path"] == (
        "<selected-fingerprint.sys.executable>"
    )
    assert subprocesses["selected-python"]["path_match"] == "exact"
    assert [argv[0] for argv in _templates().values()] == [
        "python",
        "${canonical_executable}",
        "${canonical_executable}",
        "${canonical_executable}",
        "${canonical_executable}",
        "python",
    ]


def test_fingerprint_templates_use_exact_python_then_the_canonical_executable() -> None:
    templates = _templates()
    initial = templates["initial-fingerprint"]
    final = templates["final-fingerprint"]

    assert initial[:2] == ["python", "-c"]
    assert final[:2] == ["python", "-c"]
    assert initial == final
    assert all(token not in {"python3", "py"} for argv in templates.values() for token in argv)
    assert templates["pip-check"][:3] == ["${canonical_executable}", "-m", "pip"]
    assert templates["pip-preflight"][:3] == ["${canonical_executable}", "-m", "pip"]
    assert templates["pip-install"][:3] == ["${canonical_executable}", "-m", "pip"]

    result = subprocess.run(
        [sys.executable, *initial[1:]], capture_output=True, text=True, check=False
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0, result.stderr
    assert payload == {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "base_prefix": sys.base_prefix,
        "version": list(sys.version_info[:2]),
    }


def test_templates_preserve_space_paths_and_install_only_declared_core_packages() -> None:
    templates = _templates()
    canonical = "/tmp/Python With Spaces/python"
    packages = json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))[
        "core_packages"
    ]

    for name in ("pip-check", "target-check", "pip-preflight", "pip-install"):
        resolved = [canonical if token == "${canonical_executable}" else token for token in templates[name]]
        assert resolved[0] == canonical
    assert templates["pip-preflight"][-1] == "${selected_packages}"
    assert templates["pip-install"][-1] == "${selected_packages}"
    assert packages == ["mcp>=1,<2", "PyYAML>=6", "jsonschema>=4,<5"]


@pytest.mark.parametrize("scenario", ["missing", "old", "python3-only", "py-only"])
def test_simulated_host_refuses_unusable_literal_python_without_fallback(
    scenario: str, tmp_path: Path
) -> None:
    host = _SimulatedHost(scenario, "linux")

    result = _consume_setup(host, tmp_path / "Plugin With Spaces")

    assert result in {"missing-python", "old-python"}
    assert host.mutations == 0
    assert {call[0] for call in host.calls} == {"python"}


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
@pytest.mark.parametrize(
    ("scenario", "outcome"),
    [
        ("missing-pip", "pip-check-failed"),
        ("externally-managed", "pip-preflight-failed"),
        ("non-writable", "target-check-failed"),
    ],
)
def test_simulated_native_contract_refuses_preflight_failures_without_mutation(
    platform: str, scenario: str, outcome: str, tmp_path: Path
) -> None:
    host = _SimulatedHost(scenario, platform)

    assert _consume_setup(host, tmp_path / "Plugin With Spaces") == outcome
    assert host.mutations == 0
    assert not any("--break-system-packages" in call for call in host.calls)
    assert not any("--user" in call for call in host.calls)


def test_missing_package_repairs_once_while_satisfied_package_is_not_reinstalled(
    tmp_path: Path,
) -> None:
    missing = _SimulatedHost("missing-package", "linux")
    satisfied = _SimulatedHost("satisfied", "linux", installed=True)
    plugin = tmp_path / "Plugin With Spaces"

    assert _consume_setup(missing, plugin) == "ready"
    assert missing.mutations == 1
    assert _consume_setup(satisfied, plugin) == "ready"
    assert satisfied.mutations == 0
    assert all(
        call[-3:] == ["mcp>=1,<2", "PyYAML>=6", "jsonschema>=4,<5"]
        for call in missing.calls
        if call[1:3] == ["-m", "pip"] and "install" in call
    )


@pytest.mark.parametrize("scenario", ["drift", "regression"])
def test_fingerprint_drift_or_version_regression_rejects_mcp_launch(
    scenario: str, tmp_path: Path
) -> None:
    host = _SimulatedHost(scenario, "linux")

    assert _consume_setup(host, tmp_path / "Plugin With Spaces") == "fingerprint-changed"
    assert not any(call[-1].endswith("mcp_server.py") for call in host.calls)


@pytest.mark.parametrize("host_name", ["claude", "codex"])
def test_simulated_normal_host_loads_skill_while_mcp_is_down_then_starts_packaged_mcp(
    host_name: str, tmp_path: Path
) -> None:
    plugin = tmp_path / f"{host_name} Plugin With Spaces"
    skill = plugin / "skills" / "setup-python-environment"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(SKILL.read_text(encoding="utf-8"), encoding="utf-8")
    (plugin / "mcp_server.py").write_text("packaged", encoding="utf-8")
    selected = _SimulatedHost("missing-package", "linux")

    assert (skill / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname:")
    assert _consume_setup(selected, plugin) == "ready"
    launch = selected.calls[-1]
    assert launch == ["python", str(plugin / "mcp_server.py")]
    assert len(launch) == 2
    assert selected.calls[1][0] == selected.canonical
    assert " " in selected.calls[1][0]


@pytest.mark.parametrize("platform", ["linux", "macos", "windows"])
def test_true_native_shell_free_fingerprint_when_host_is_available(
    platform: str, tmp_path: Path
) -> None:
    native = "windows" if sys.platform == "win32" else "macos" if sys.platform == "darwin" else "linux"
    if platform != native:
        # famulus-skip: category=platform-contract; reason=true native execution requires the named operating system; alternate=the simulated matrix above executes every refusal and repair branch for all three platform labels
        pytest.skip(f"true native {platform} host unavailable on {native}")
    if native == "windows":
        # famulus-skip: category=capability-unavailable; reason=this checkout has no native Windows executable fixture; alternate=the simulated Windows matrix covers ordered argv and no-mutation branches
        pytest.skip("native Windows executable fixture is unavailable in this checkout")
    selected_bin = tmp_path / "Selected Python Bin"
    selected_bin.mkdir()
    (selected_bin / "python").symlink_to(Path(sys.executable).resolve())
    environment = {**__import__("os").environ, "PATH": str(selected_bin)}

    result = subprocess.run(
        _templates()["initial-fingerprint"],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    canonical = json.loads(result.stdout)["executable"]
    assert " " in canonical
    second = subprocess.run(
        [canonical, "-c", "print('selected')"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert second.returncode == 0
    assert second.stdout == "selected\n"
