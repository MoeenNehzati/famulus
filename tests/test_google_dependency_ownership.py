from __future__ import annotations

import json
import importlib.util
import re
from pathlib import Path
import socket
import subprocess
import sys
from dataclasses import dataclass, field
import urllib.request
import webbrowser

import pytest
import yaml

from officina.credentials import google as google_credentials
from officina.credentials import secret_store
from officina.runtime.python_machine_interface import PythonMachineInterface


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "references" / "blueprint-schema" / "runtime_dependencies.json"
GOOGLE_OWNERS = ("connect-google", "cloud-files", "online-calendar", "email-client")
REPAIR_INTERFACE = {
    "interface": "setup-python-environment.interface.repair-selected-packages",
    "version": 1,
}


def _load_syncer():
    path = ROOT / "skills" / "skill-maker" / "_rtx" / "_blueprint_syncer.py"
    spec = importlib.util.spec_from_file_location("task10a_blueprint_syncer", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def deny_external_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    attempts: list[str] = []

    def deny(name: str):
        def blocked(*_args, **_kwargs):
            attempts.append(name)
            raise AssertionError(f"controlled owner harness attempted real {name}")

        return blocked

    monkeypatch.setattr(subprocess, "run", deny("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", deny("subprocess.Popen"))
    monkeypatch.setattr(socket, "socket", deny("socket.socket"))
    monkeypatch.setattr(urllib.request, "urlopen", deny("urllib.request.urlopen"))
    monkeypatch.setattr(webbrowser, "open", deny("webbrowser.open"))
    monkeypatch.setattr(secret_store, "store", deny("credential store"))
    monkeypatch.setattr(secret_store, "clear", deny("credential clear"))
    monkeypatch.setattr(
        google_credentials, "create_credential_file", deny("OAuth credential file")
    )
    monkeypatch.setattr(
        google_credentials, "exchange_authorization_code", deny("OAuth token exchange")
    )
    monkeypatch.setattr(PythonMachineInterface, "dispatch", deny("service dispatch"))
    return attempts


def _service_owners() -> dict[str, str]:
    path = ROOT / "skills" / "connect-google" / "_rtx" / "_connect_services.py"
    spec = importlib.util.spec_from_file_location("task10a_connect_services", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return {
        service: dispatch.target_module_id.removesuffix("._rtx")
        for service, dispatch in module.SERVICE_DISPATCHES.items()
    }


def _task2_templates() -> dict[str, list[str]]:
    text = (ROOT / "skills" / "setup-python-environment" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    return {
        name: json.loads(payload)
        for name, payload in re.findall(
            r"<!-- command:([a-z-]+) -->\n```json\n([^`]+)```", text
        )
    }


def _authored_skill(owner: str) -> str:
    text = (ROOT / "skills" / owner / "SKILL.md").read_text(encoding="utf-8")
    return text.split("<!-- END BLUEPRINT INTERFACES -->", 1)[1]


def _owner_packages(owner: str) -> tuple[str, ...]:
    module = json.loads(MANIFEST.read_text(encoding="utf-8"))["skills"][owner]
    return tuple(
        sorted(
            {
                dependency["name"]
                for interface in module["interfaces"].values()
                for dependency in interface["dependencies"]
                if dependency["kind"] == "python-package"
            },
            key=str.casefold,
        )
    )


def _expand(argv: list[str], canonical: str, packages: tuple[str, ...]) -> list[str]:
    result: list[str] = []
    for token in argv:
        if token == "${canonical_executable}":
            result.append(canonical)
        elif token == "${selected_packages}":
            result.extend(packages)
        else:
            result.append(token)
    return result


@dataclass
class _OwnerHarness:
    failure: str | None = None
    failure_owner: str | None = None
    installed: set[str] = field(default_factory=set)
    declarations: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    calls: list[tuple[str, list[str]]] = field(default_factory=list)
    fingerprints: list[tuple[str, dict[str, object], dict[str, object]]] = field(
        default_factory=list
    )
    installs: list[tuple[str, tuple[str, ...]]] = field(default_factory=list)
    boundary_actions: list[tuple[str, str]] = field(default_factory=list)
    repaired_owners: set[str] = field(default_factory=set)
    fingerprint_counts: dict[str, int] = field(default_factory=dict)
    canonical: str = "/tmp/Selected Python/python"

    def run(self, owner: str, argv: list[str]) -> tuple[int, str]:
        self.calls.append((owner, argv))
        active_failure = self.failure if self.failure_owner in {None, owner} else None
        if argv[0] == "python":
            self.fingerprint_counts[owner] = self.fingerprint_counts.get(owner, 0) + 1
            if active_failure == "missing-python" and self.fingerprint_counts[owner] == 1:
                raise FileNotFoundError("python")
            executable = self.canonical
            version = [3, 13]
            if active_failure == "old-python" and self.fingerprint_counts[owner] == 1:
                version = [3, 10]
            if active_failure == "relative-python" and self.fingerprint_counts[owner] == 1:
                executable = "relative/python"
            if active_failure == "fingerprint-drift" and self.fingerprint_counts[owner] == 2:
                executable = "/tmp/Other Python/python"
            fingerprint = {
                "executable": executable,
                "prefix": "/tmp/Selected Python",
                "base_prefix": "/tmp/Selected Python",
                "version": version,
            }
            return 0, json.dumps(fingerprint)
        if argv[1:4] == ["-m", "pip", "--version"]:
            return (1, "missing pip") if active_failure == "missing-pip" else (0, "pip 25")
        if argv[1] == "-c":
            return (1, "not writable") if active_failure == "non-writable" else (0, "writable")
        if "--dry-run" in argv:
            if active_failure == "pip-refusal":
                return 1, "externally managed"
            pending = [] if set(argv[-1:]).issubset(self.installed) else [
                {"metadata": {"name": name}} for name in argv[-1:]
            ]
            return 0, json.dumps({"install": pending})
        packages = tuple(argv[-1:])
        self.installs.append((owner, packages))
        self.installed.update(packages)
        return 0, json.dumps(
            {"install": [{"metadata": {"name": name}} for name in packages]}
        )

    def cross_boundary(
        self, boundary: str, owner: str, required_owners: tuple[str, ...]
    ) -> None:
        missing = set(required_owners) - self.repaired_owners
        if missing:
            raise AssertionError(
                f"{boundary} boundary reached before successful repair: {sorted(missing)}"
            )
        self.boundary_actions.append((boundary, owner))


def _repair_owner(harness: _OwnerHarness, owner: str) -> bool:
    templates = _task2_templates()
    packages = _owner_packages(owner)
    harness.declarations.append((owner, packages))
    try:
        code, output = harness.run(owner, templates["initial-fingerprint"])
    except FileNotFoundError:
        return False
    if code:
        return False
    initial = json.loads(output)
    canonical = initial["executable"]
    if initial.get("version", []) < [3, 11] or not Path(canonical).is_absolute():
        return False
    for name in ("pip-check", "target-check"):
        code, _ = harness.run(owner, _expand(templates[name], canonical, packages))
        if code:
            return False
    code, output = harness.run(
        owner, _expand(templates["pip-preflight"], canonical, packages)
    )
    if code:
        return False
    if json.loads(output)["install"]:
        code, _ = harness.run(
            owner, _expand(templates["pip-install"], canonical, packages)
        )
        if code:
            return False
    code, output = harness.run(owner, templates["final-fingerprint"])
    if code:
        return False
    final = json.loads(output)
    harness.fingerprints.append((owner, initial, final))
    valid = final == initial and final["version"] >= [3, 11]
    if valid:
        harness.repaired_owners.add(owner)
    return valid


def _run_selection(
    harness: _OwnerHarness, services: tuple[str, ...], *, google_selected: bool = True
) -> bool:
    if not google_selected:
        return True
    service_owners = _service_owners()
    owners = ("connect-google", *(service_owners[service] for service in services))
    for owner in owners:
        if not _repair_owner(harness, owner):
            return False
    for owner in owners:
        boundary = "google-credential-network" if owner == "connect-google" else "service"
        harness.cross_boundary(boundary, owner, owners)
    return True


def test_google_owners_are_optional_and_declare_only_keyring() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    for owner in GOOGLE_OWNERS:
        module = manifest["skills"][owner]
        packages = {
            dependency["name"]
            for interface in module["interfaces"].values()
            for dependency in interface["dependencies"]
            if dependency["kind"] == "python-package"
        }
        assert module["installation_tier"] == "optional"
        assert packages == {"keyring"}


def test_google_owner_gateways_require_selected_python_repair() -> None:
    for owner in GOOGLE_OWNERS:
        gateway = yaml.safe_load(
            (ROOT / "skills" / owner / "blueprints" / "gateway.yaml").read_text(
                encoding="utf-8"
            )
        )
        assert REPAIR_INTERFACE in gateway["uses_interfaces"]
        assert REPAIR_INTERFACE in next(iter(gateway["interfaces"].values()))["uses_interfaces"]


def test_actual_owner_instructions_put_repair_before_external_boundaries() -> None:
    boundary_markers = {
        "connect-google": (
            "1. Use `connect-google._rtx.interface.client-status`",
            "`connect-google.interface.connect-services`",
        ),
        "cloud-files": (
            "For shared Google setup or Drive reauthorization",
            "Use `lists-read`, `lists-write`, `lists-delete`",
        ),
        "online-calendar": ("Use `online-calendar._rtx.interface.scripts-gcal`",),
        "email-client": (
            "Use `email-client._rtx.interface.mail-list`",
            "For shared Google setup or Gmail reauthorization",
        ),
    }
    repair_marker = "`setup-python-environment.interface.repair-selected-packages`"

    for owner, markers in boundary_markers.items():
        authored = _authored_skill(owner)
        repair_index = authored.index(repair_marker)
        assert all(repair_index < authored.index(marker) for marker in markers)
        repair_paragraph = next(
            paragraph
            for paragraph in authored.split("\n\n")
            if repair_marker in paragraph
        )
        assert repair_paragraph.startswith(("Before ", "\nBefore "))
        assert "[\"keyring\"]" in repair_paragraph
        assert "failure" in repair_paragraph.casefold()


def test_generated_google_owner_contracts_expose_selected_python_repair() -> None:
    syncer = _load_syncer()
    blueprints = syncer.load_blueprints()

    for owner in GOOGLE_OWNERS:
        blueprint = blueprints[owner]
        block = syncer.generated_contract_block(
            owner, blueprint.data, blueprint.repository_graph
        )
        assert (
            f"`{owner}.source.gateway -> "
            "setup-python-environment.interface.repair-selected-packages@1`"
        ) in block


def test_core_does_not_own_google_packages() -> None:
    core_packages = json.loads((ROOT / "mcp-core.json").read_text(encoding="utf-8"))[
        "core_packages"
    ]
    assert core_packages == ["mcp>=1,<2", "PyYAML>=6", "jsonschema>=4,<5"]


def test_core_only_runs_no_google_owner_or_package_procedure(
    deny_external_calls: list[str],
) -> None:
    harness = _OwnerHarness()

    assert _run_selection(harness, (), google_selected=False)
    assert harness.declarations == []
    assert harness.calls == []
    assert harness.installs == []
    assert harness.boundary_actions == []
    assert deny_external_calls == []


@pytest.mark.parametrize(
    ("services", "expected_owners"),
    [
        ((), ("connect-google",)),
        (("drive",), ("connect-google", "cloud-files")),
        (("calendar",), ("connect-google", "online-calendar")),
        (("gmail",), ("connect-google", "email-client")),
        (("drive", "calendar"), ("connect-google", "cloud-files", "online-calendar")),
        (("drive", "gmail"), ("connect-google", "cloud-files", "email-client")),
        (("calendar", "gmail"), ("connect-google", "online-calendar", "email-client")),
        (
            ("drive", "calendar", "gmail"),
            ("connect-google", "cloud-files", "online-calendar", "email-client"),
        ),
    ],
)
def test_selected_owner_composition_is_isolated_and_deduplicated(
    services: tuple[str, ...],
    expected_owners: tuple[str, ...],
    deny_external_calls: list[str],
) -> None:
    harness = _OwnerHarness()

    assert _run_selection(harness, services)
    assert harness.declarations == [(owner, ("keyring",)) for owner in expected_owners]
    assert {package for _, packages in harness.declarations for package in packages} == {
        "keyring"
    }
    assert harness.installs == [("connect-google", ("keyring",))]
    assert [owner for _, owner in harness.boundary_actions] == list(expected_owners)
    assert {
        owner for owner in GOOGLE_OWNERS if owner not in expected_owners
    }.isdisjoint(owner for owner, _ in harness.calls)
    assert all(initial == final for _, initial, final in harness.fingerprints)
    assert all(
        argv[0] in {"python", harness.canonical}
        for _, argv in harness.calls
    )
    assert all(
        token not in {"python3", "py"}
        for _, argv in harness.calls
        for token in argv
    )
    assert deny_external_calls == []


def test_satisfied_selected_declarations_recheck_without_reinstall(
    deny_external_calls: list[str],
) -> None:
    harness = _OwnerHarness(installed={"keyring"})

    assert _run_selection(harness, ("drive", "calendar", "gmail"))
    assert harness.installs == []
    assert [owner for owner, _ in harness.declarations] == list(GOOGLE_OWNERS)
    assert all(initial == final for _, initial, final in harness.fingerprints)
    assert deny_external_calls == []


@pytest.mark.parametrize("failure", ["missing-python", "old-python", "relative-python"])
def test_initial_python_gate_stops_before_canonical_or_external_actions(
    failure: str, deny_external_calls: list[str]
) -> None:
    harness = _OwnerHarness(failure=failure, failure_owner="connect-google")

    assert not _run_selection(harness, ("drive",))
    assert harness.declarations == [("connect-google", ("keyring",))]
    assert harness.calls == [
        ("connect-google", _task2_templates()["initial-fingerprint"])
    ]
    assert harness.installs == []
    assert harness.boundary_actions == []
    assert deny_external_calls == []


@pytest.mark.parametrize(
    "failure", ["missing-pip", "non-writable", "pip-refusal", "fingerprint-drift"]
)
@pytest.mark.parametrize("failure_owner", ["connect-google", "cloud-files"])
def test_selected_python_failure_precedes_every_external_boundary(
    failure: str, failure_owner: str, deny_external_calls: list[str]
) -> None:
    harness = _OwnerHarness(
        failure=failure,
        failure_owner=failure_owner,
        installed={"keyring"},
    )

    assert not _run_selection(harness, ("drive",))
    expected = ["connect-google"] if failure_owner == "connect-google" else [
        "connect-google",
        "cloud-files",
    ]
    assert harness.declarations == [(owner, ("keyring",)) for owner in expected]
    assert harness.installs == []
    assert harness.boundary_actions == []
    assert deny_external_calls == []
