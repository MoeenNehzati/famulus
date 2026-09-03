from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
from dataclasses import dataclass, field

import pytest

from officina.blueprints.graph import load_repository_blueprint_graph


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "setup-python-environment" / "SKILL.md"
CANDIDATES = ("python", "python3", "py")


def _templates() -> dict[str, list[str]]:
    text = SKILL.read_text(encoding="utf-8")
    return {
        name: json.loads(payload)
        for name, payload in re.findall(
            r"<!-- command:([a-z-]+) -->\n```json\n([^`]+)```", text
        )
    }


def _core_packages() -> list[str]:
    return json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))[
        "core_packages"
    ]


def _expand(argv: list[str], bindings: dict[str, str], packages: list[str]) -> list[str]:
    expanded: list[str] = []
    for token in argv:
        if token == "${selected_packages}":
            expanded.extend(packages)
        else:
            expanded.append(bindings.get(token, token))
    return expanded


@dataclass
class _SimulatedHost:
    """Simulates the core setup route: discover, build, repair, verify, launch."""

    scenario: str
    platform: str = "linux"
    venv_root: str = "/tmp/Famulus State/venv"
    calls: list[list[str]] = field(default_factory=list)
    mutations: int = 0
    environments_created: int = 0
    installed: bool = False
    fingerprint_calls: int = 0

    @property
    def canonical(self) -> str:
        return f"{self.venv_root}/bin/python"

    def available(self, command: str) -> list[int] | None:
        """Version of a candidate command, or None when it does not exist."""
        if self.scenario == "no-interpreter":
            return None
        if self.scenario == "all-old":
            return [3, 10]
        if self.scenario == "python3-only" and command != "python3":
            return None
        if self.scenario == "py-only" and command != "py":
            return None
        return [3, 13]

    def run(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        self.calls.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            self.environments_created += 1
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:2] == ["-c"] and "base_prefix" in argv[2]:
            return self._fingerprint(argv)
        if argv[1:4] == ["-m", "pip", "--version"]:
            code = 1 if self.scenario == "missing-pip" else 0
            return subprocess.CompletedProcess(argv, code, "pip 25", "pip unavailable")
        if argv[1] == "-c":
            code = 1 if self.scenario == "non-writable" else 0
            return subprocess.CompletedProcess(argv, code, "", "target is not writable")
        if "--dry-run" in argv:
            if self.scenario == "externally-managed":
                return subprocess.CompletedProcess(argv, 1, "", "externally managed")
            pending = [] if self.scenario == "satisfied" else [{"metadata": {"name": "mcp"}}]
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"install": pending}), ""
            )
        if argv[1:3] == ["-m", "pip"]:
            self.mutations += 1
            self.installed = True
            return subprocess.CompletedProcess(
                argv, 0, json.dumps({"install": [{"metadata": {"name": "mcp"}}]}), ""
            )
        return subprocess.CompletedProcess(argv, 0 if self.installed else 1, "", "")

    def _fingerprint(self, argv: list[str]) -> subprocess.CompletedProcess[str]:
        if argv[0] in CANDIDATES:
            version = self.available(argv[0])
            if version is None:
                raise FileNotFoundError(argv[0])
            return subprocess.CompletedProcess(
                argv,
                0,
                json.dumps(
                    {
                        "executable": f"/usr/bin/{argv[0]}",
                        "prefix": "/usr",
                        "base_prefix": "/usr",
                        "version": version,
                    }
                ),
                "",
            )
        self.fingerprint_calls += 1
        executable = self.canonical
        version = [3, 13]
        if self.scenario == "drift" and self.fingerprint_calls == 2:
            executable = "/tmp/Other Venv/bin/python"
        if self.scenario == "regression" and self.fingerprint_calls == 2:
            version = [3, 10]
        return subprocess.CompletedProcess(
            argv,
            0,
            json.dumps(
                {
                    "executable": executable,
                    "prefix": self.venv_root,
                    "base_prefix": "/usr",
                    "version": version,
                }
            ),
            "",
        )


def _consume_setup(host: _SimulatedHost, plugin: Path) -> str:
    """Walk the core setup route exactly as SKILL.md orders it."""
    templates = _templates()
    packages = _core_packages()

    discovered: list[tuple[list[int], str]] = []
    for command in CANDIDATES:
        try:
            probe = host.run(_expand(templates["candidate-fingerprint"], {"${candidate}": command}, packages))
        except FileNotFoundError:
            continue
        if probe.returncode:
            continue
        payload = json.loads(probe.stdout)
        if payload["version"] >= [3, 11] and Path(payload["executable"]).is_absolute():
            discovered.append((payload["version"], payload["executable"]))
    if not discovered:
        return "ask-user-for-interpreter"
    host_python = max(discovered)[1]

    created = host.run(
        _expand(
            templates["create-venv"],
            {"${host_python}": host_python, "${venv_root}": host.venv_root},
            packages,
        )
    )
    if created.returncode:
        return "create-venv-failed"

    selected_probe = host.run(
        _expand(templates["candidate-fingerprint"], {"${candidate}": host.canonical}, packages)
    )
    selected = json.loads(selected_probe.stdout)
    if selected["prefix"] == selected["base_prefix"]:
        return "not-dedicated"
    canonical = selected["executable"]

    bindings = {"${canonical_executable}": canonical}
    for name in ("pip-check", "target-check"):
        if host.run(_expand(templates[name], bindings, packages)).returncode:
            return name + "-failed"
    preflight = host.run(_expand(templates["pip-preflight"], bindings, packages))
    if preflight.returncode:
        return "pip-preflight-failed"
    if json.loads(preflight.stdout)["install"]:
        if host.run(_expand(templates["pip-install"], bindings, packages)).returncode:
            return "pip-install-failed"

    final = json.loads(
        host.run(
            _expand(templates["candidate-fingerprint"], {"${candidate}": canonical}, packages)
        ).stdout
    )
    if final["version"] < [3, 11] or final != selected:
        return "fingerprint-changed"

    server = host.run([canonical, str(plugin / "mcp_server.py")])
    return "ready" if server.returncode == 0 else "mcp-start-failed"


def test_setup_skill_is_host_loaded_and_uses_task_1_core_authority() -> None:
    graph = load_repository_blueprint_graph(
        ROOT,
        schema_root=ROOT / "references" / "blueprint-schema",
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

    assert subprocesses["candidate-python"]["path"] == "<discovered-candidate>"
    assert subprocesses["candidate-python"]["path_match"] == "exact"
    assert subprocesses["selected-python"]["path"] == (
        "<selected-fingerprint.sys.executable>"
    )
    assert subprocesses["selected-python"]["path_match"] == "exact"
    assert [argv[0] for argv in _templates().values()] == [
        "${candidate}",
        "${host_python}",
        "${canonical_executable}",
        "${canonical_executable}",
        "${canonical_executable}",
        "${canonical_executable}",
    ]


def test_the_core_route_may_prompt_while_unattended_callers_get_an_outcome() -> None:
    graph = load_repository_blueprint_graph(
        ROOT,
        schema_root=ROOT / "references" / "blueprint-schema",
    )
    contract = graph.nodes[
        "setup-python-environment.source.gateway"
    ].declaration["interfaces"][
        "setup-python-environment.source.gateway.interface.default"
    ]["contract"]

    assert contract["interaction"]["mode"] == "interactive"
    assert contract["interaction"]["unattended_outcome"] == "prerequisite-failed"
    assert {outcome["id"] for outcome in contract["outcomes"]} == {
        "repaired",
        "prerequisite-failed",
    }


def test_fingerprint_template_binds_a_candidate_and_reports_the_four_fields() -> None:
    templates = _templates()
    fingerprint = templates["candidate-fingerprint"]

    assert fingerprint[:2] == ["${candidate}", "-c"]
    assert templates["create-venv"] == ["${host_python}", "-m", "venv", "${venv_root}"]
    assert all(
        token not in {"python", "python3", "py"}
        for argv in templates.values()
        for token in argv
    )
    assert templates["pip-check"][:3] == ["${canonical_executable}", "-m", "pip"]
    assert templates["pip-preflight"][:3] == ["${canonical_executable}", "-m", "pip"]
    assert templates["pip-install"][:3] == ["${canonical_executable}", "-m", "pip"]

    result = subprocess.run(
        [sys.executable, *fingerprint[1:]], capture_output=True, text=True, check=False
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
    packages = _core_packages()

    for name in ("pip-check", "target-check", "pip-preflight", "pip-install"):
        resolved = [
            canonical if token == "${canonical_executable}" else token
            for token in templates[name]
        ]
        assert resolved[0] == canonical
    assert templates["pip-preflight"][-1] == "${selected_packages}"
    assert templates["pip-install"][-1] == "${selected_packages}"
    assert packages == ["mcp>=1,<2", "PyYAML>=6", "jsonschema>=4,<5"]


@pytest.mark.parametrize("scenario", ["python3-only", "py-only"])
def test_a_machine_without_literal_python_is_now_set_up_rather_than_refused(
    scenario: str, tmp_path: Path
) -> None:
    host = _SimulatedHost(scenario)

    assert _consume_setup(host, tmp_path / "Plugin With Spaces") == "ready"
    assert host.environments_created == 1
    assert host.calls[-1][0] == host.canonical


@pytest.mark.parametrize("scenario", ["no-interpreter", "all-old"])
def test_no_usable_interpreter_asks_the_user_and_never_installs_python(
    scenario: str, tmp_path: Path
) -> None:
    host = _SimulatedHost(scenario)

    assert _consume_setup(host, tmp_path / "Plugin With Spaces") == (
        "ask-user-for-interpreter"
    )
    assert host.mutations == 0
    assert host.environments_created == 0
    assert not any("-m" in call and "venv" in call for call in host.calls)


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
    missing = _SimulatedHost("missing-package")
    satisfied = _SimulatedHost("satisfied", installed=True)
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
    host = _SimulatedHost(scenario)

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
    selected = _SimulatedHost("missing-package")

    assert (skill / "SKILL.md").read_text(encoding="utf-8").startswith("---\nname:")
    assert _consume_setup(selected, plugin) == "ready"
    launch = selected.calls[-1]
    assert launch == [selected.canonical, str(plugin / "mcp_server.py")]
    assert len(launch) == 2
    assert launch[0] != "python"
    assert " " in launch[1]


def test_true_native_dedicated_environment_fingerprints_as_its_own_environment(
    tmp_path: Path,
) -> None:
    if sys.platform == "win32":
        # famulus-skip: category=capability-unavailable; reason=this checkout has no native Windows executable fixture; alternate=the simulated matrix covers ordered argv and no-mutation branches on the Windows label
        pytest.skip("native Windows executable fixture is unavailable in this checkout")
    venv_root = tmp_path / "Famulus State With Spaces" / "venv"
    templates = _templates()
    subprocess.run(
        [
            token.replace("${host_python}", sys.executable).replace(
                "${venv_root}", str(venv_root)
            )
            for token in templates["create-venv"]
        ],
        check=True,
        capture_output=True,
    )
    canonical = venv_root / ("Scripts" if os.name == "nt" else "bin") / "python"

    result = subprocess.run(
        [
            token.replace("${candidate}", str(canonical))
            for token in templates["candidate-fingerprint"]
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert Path(payload["executable"]).is_absolute()
    assert " " in payload["executable"]
    assert payload["prefix"] != payload["base_prefix"]
    assert payload["version"] >= [3, 11]
