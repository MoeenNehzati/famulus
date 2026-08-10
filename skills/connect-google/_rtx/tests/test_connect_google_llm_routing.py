from __future__ import annotations

from pathlib import Path

import pytest
import yaml


SKILL_ROOT = Path(__file__).resolve().parents[2]


def load(relative: str) -> dict[str, object]:
    return yaml.safe_load((SKILL_ROOT / relative).read_text(encoding="utf-8"))


def source(
    module_root: Path,
    root: dict[str, object],
    source_id: str,
) -> dict[str, object]:
    locator = root["sources"][source_id]["blueprint"]
    assert locator["base"] == "module-root"
    node = yaml.safe_load(
        (module_root / locator["path"]).read_text(encoding="utf-8")
    )
    assert node["id"] == source_id
    return node


def exported_source(
    root: dict[str, object], export_id: str
) -> tuple[dict[str, object], dict[str, object]]:
    module_id = export_id.split(".interface.", 1)[0]
    module_root = SKILL_ROOT
    module = root
    current_id = str(root["id"])
    for segment in module_id.split(".")[1:]:
        assert segment in module["children"]
        module_root /= segment
        module = yaml.safe_load(
            (module_root / "blueprint.yaml").read_text(encoding="utf-8")
        )
        current_id = f"{current_id}.{segment}"
        assert module["id"] == current_id
    return _exported_source(module_root, module, export_id)


def _exported_source(
    module_root: Path,
    root: dict[str, object],
    export_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    export = root["exports"][export_id]
    source_interface = export["source_interface"]
    source_id, _, _ = source_interface.rpartition(".interface.")
    node = source(module_root, root, source_id)
    return node, node["interfaces"][source_interface]


def body(relative: str) -> str:
    text = (SKILL_ROOT / relative).read_text(encoding="utf-8")
    for block in ("CONTRACT", "INTERFACES"):
        begin = f"<!-- BEGIN BLUEPRINT {block} -->"
        end = f"<!-- END BLUEPRINT {block} -->"
        if begin in text and end in text:
            prefix, remainder = text.split(begin, 1)
            _, suffix = remainder.split(end, 1)
            text = prefix + suffix
    return text.lower()


@pytest.fixture(scope="module")
def root_blueprint() -> dict[str, object]:
    """Parse the immutable root graph once for all routing assertions."""
    return load("blueprint.yaml")


@pytest.fixture(scope="module")
def skill_body() -> str:
    return body("SKILL.md")


@pytest.fixture(scope="module")
def create_client_body() -> str:
    return body("instructions/create-client.md")


@pytest.fixture(scope="module")
def connect_services_body() -> str:
    return body("instructions/connect-services.md")


def test_module_and_markdown_gateway_graph(
    root_blueprint: dict[str, object],
) -> None:
    root = root_blueprint
    default, default_interface = exported_source(
        root, "connect-google.interface.default"
    )
    create_client, create_client_interface = exported_source(
        root, "connect-google.interface.create-client"
    )
    connect_services, connect_services_interface = exported_source(
        root, "connect-google.interface.connect-services"
    )

    assert root["schema_version"] == 6
    assert root["node_type"] == "module"
    assert root["id"] == "connect-google"
    assert root["discovery"] == {
        "mechanism": "skill",
        "catalog": {
            "domain": "assistant-operations",
            "topics": ["external-integrations"],
            "visibility": "listed",
        },
        "activated_by": ["user-request", "skill-workflow"],
        "persistent_modifier": False,
    }
    assert not (SKILL_ROOT / ".SKILL.md.blueprint.yaml").exists()
    default_uses = {
        (entry["interface"], entry["version"])
        for entry in default["uses_interfaces"]
    }
    semantic_default_uses = {
        ("connect-google._rtx.interface.bind-credential-file", 1),
        ("connect-google._rtx.interface.client-status", 1),
        ("connect-google._rtx.interface.connect-services", 1),
        ("connect-google._rtx.interface.install-client", 1),
        (
            "connect-google.source.instructions-connect-services"
            ".interface.connect-services",
            1,
        ),
        (
            "connect-google.source.instructions-create-client"
            ".interface.create-client",
            1,
        ),
    }
    assert default_uses == semantic_default_uses
    assert create_client["uses_interfaces"] == [
        {
            "interface": (
                "connect-google.source.instructions-connect-services"
                ".interface.connect-services"
            ),
            "version": 1,
        }
    ]
    assert connect_services["uses_interfaces"] == [
        {"interface": name, "version": 1}
        for name in (
            "connect-google._rtx.interface.bind-credential-file",
            "connect-google._rtx.interface.client-status",
            "connect-google._rtx.interface.connect-services",
            "connect-google._rtx.interface.install-client",
        )
    ]

    assert set(root["exports"]) == {
        "connect-google.interface.default",
        "connect-google.interface.create-client",
        "connect-google.interface.connect-services",
    }
    child = load("_rtx/blueprint.yaml")
    assert set(child["exports"]) == {
        "connect-google._rtx.interface.client-status",
        "connect-google._rtx.interface.install-client",
        "connect-google._rtx.interface.connect-services",
        "connect-google._rtx.interface.bind-credential-file",
    }
    assert default_interface["version"] == 1
    assert create_client_interface["version"] == 1
    assert connect_services_interface["version"] == 1
    _, coordinator_interface = _exported_source(
        SKILL_ROOT / "_rtx",
        child,
        "connect-google._rtx.interface.connect-services",
    )
    coordinator_binding = coordinator_interface["process_binding"]["patterns"][0]
    assert {"--no-open-browser", "--callback-port"} <= set(
        coordinator_binding["allowed_flags"]
    )
    assert coordinator_binding["flag_patterns"]["--callback-port"] == "^[0-9]+$"
    assert coordinator_interface["contract"]["interaction"] == {
        "mode": "interactive",
        "channel": "tty",
        "unattended_outcome": "failed",
    }

    for node in (default, create_client, connect_services):
        for edge in node.get("uses_interfaces", []):
            assert not edge["interface"].startswith(
                ("cloud-files.", "g-calendar.", "email-client.")
            )


def test_client_status_declares_every_google_client_path_it_reads(
    root_blueprint: dict[str, object],
) -> None:
    root = root_blueprint
    _, node = exported_source(root, "connect-google._rtx.interface.client-status")

    declared_paths = {
        entry["path"] for entry in node["contract"]["direct_io"]["reads"]
    }

    assert declared_paths == {
        "platform Famulus config root (see common.interface.google-credentials) / connect-google/client.json",
        "$HOME/.config/cloud-files/client.json",
        "$HOME/.config/g-calendar/client.json",
    }
    assert node["process_binding"]["patterns"][0]["flag_patterns"] == {
        "--home": "^.+$"
    }


def test_install_client_patterns_require_values_for_value_bearing_flags(
    root_blueprint: dict[str, object],
) -> None:
    root = root_blueprint
    _, node = exported_source(root, "connect-google._rtx.interface.install-client")

    assert node["process_binding"]["patterns"][0]["flag_patterns"] == {
        "--from-json": "^.+$",
        "--home": "^.+$",
    }


def test_authorize_services_declares_interactive_portable_oauth_contract() -> None:
    child = load("_rtx/blueprint.yaml")
    node = source(
        SKILL_ROOT / "_rtx",
        child,
        "connect-google._rtx.source.rtx-authorize-services",
    )
    interface = node["interfaces"][
        "connect-google._rtx.source.rtx-authorize-services.interface.authorize-services"
    ]
    contract = interface["contract"]
    binding = interface["process_binding"]["patterns"][0]

    assert contract["interaction"] == {
        "mode": "interactive",
        "channel": "tty",
        "unattended_outcome": "failed",
    }
    assert set(binding["allowed_flags"]) == {
        "--services",
        "--account-hint",
        "--home",
        "--no-open-browser",
        "--callback-port",
    }
    assert binding["flag_patterns"]["--callback-port"] == "^[0-9]+$"
    assert "_browser_helper\\.py" in node["content"]

    network = {entry["id"]: entry for entry in contract["direct_io"]["network"]}
    assert network["authorization-endpoint"]["endpoint"] == (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    assert network["token-endpoint"]["endpoint"] == (
        "https://oauth2.googleapis.com/token"
    )
    assert network["userinfo-endpoint"]["endpoint"] == (
        "https://openidconnect.googleapis.com/v1/userinfo"
    )
    assert network["browser-consent"]["endpoint"] == "http://127.0.0.1:<port>/"

    writes = {entry["id"]: entry for entry in contract["direct_io"]["writes"]}
    assert writes["diagnostic-stream"] == {
        "id": "diagnostic-stream",
        "medium": "stderr",
        "access": "write",
        "system": "agent-runtime",
        "content": "oauth-phase-diagnostics",
        "formats": ["jsonl"],
        "sensitivity": "user-private",
        "reason": (
            "Emit stable phase and code events, the manual authorization URL, "
            "same-port SSH forwarding guidance, and browser-launch status without "
            "OAuth secrets."
        ),
    }
    atomicity = contract["execution"]["mutation_safety"]["atomicity"][
        "per_effect_only"
    ]
    assert "descriptor" in atomicity
    assert "secret" in atomicity


def test_default_router_contract(skill_body: str) -> None:
    text = skill_body
    assert text.startswith("---")
    assert "skill: connect-google" in text
    assert "client-status" in text
    assert "create-client" in text
    assert "connect-services" in text
    assert "connect" in text and "reconnect" in text
    assert "drive" in text and "calendar" in text and "gmail" in text
    assert "credential file" in text
    assert "complete: true" in text
    assert "never commit" in text
    assert "dispatcher " not in text
    assert "connect-google-rtx" not in text


def test_create_client_route_contract(create_client_body: str) -> None:
    text = create_client_body
    for phrase in (
        "external",
            "testing",
            "test users",
            "100",
            "user cap",
            "seven days",
        "drive api",
        "calendar api",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/calendar",
        "https://mail.google.com/",
        "desktop",
        "workspace administrator",
        "connect-services",
    ):
        assert phrase in text
    assert "test-user allowlist" in text
    assert "does not distribute" in text
    assert "never commit" in text
    assert "dispatcher " not in text
    assert "connect-google-rtx" not in text


def test_connect_services_route_contract(connect_services_body: str) -> None:
    text = connect_services_body
    for phrase in (
        "recommend all three",
        "subset",
        "service-owned",
        "credential file",
        "complete: true",
        "bind-credential-file",
        "same file",
        "manual authorization url",
        "--no-open-browser",
        "--callback-port",
        "ssh",
    ):
        assert phrase in text
    assert "credential_id" not in text
    assert "dispatcher " not in text
    assert "connect-google-rtx" not in text
    assert "client_secret" not in text
