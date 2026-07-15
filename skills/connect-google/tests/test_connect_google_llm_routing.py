from __future__ import annotations

from pathlib import Path

import yaml


SKILL_ROOT = Path(__file__).resolve().parents[1]


def load(relative: str) -> dict[str, object]:
    return yaml.safe_load((SKILL_ROOT / relative).read_text(encoding="utf-8"))


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


def test_root_and_llm_interface_graph() -> None:
    root = load("blueprint.yaml")
    default = root["default_interface"]
    create_client = load("llm_interfaces/.create-client.md.blueprint.yaml")
    connect_services = load("llm_interfaces/.connect-services.md.blueprint.yaml")

    assert root["id"] == "connect-google"
    assert root["category"] == "workflow-general-assistant"
    assert root["role"] == "integration"
    assert root["kind"] == "setup"
    assert not (SKILL_ROOT / ".SKILL.md.blueprint.yaml").exists()
    assert default["uses_interfaces"] == [
        {"interface": "connect-google.machine.client-status", "version": 1},
        {"interface": "connect-google.llm.create-client", "version": 1},
        {"interface": "connect-google.llm.connect-services", "version": 1},
    ]
    assert create_client["uses_interfaces"] == [
        {"interface": "connect-google.llm.connect-services", "version": 1}
    ]
    assert connect_services["uses_interfaces"] == [
        {"interface": name, "version": 1}
        for name in (
            "connect-google.machine.client-status",
            "connect-google.machine.install-client",
        )
    ]

    interface_ids = {edge["interface"] for edge in root["interfaces"]}
    assert interface_ids == {
        "connect-google.llm.create-client",
        "connect-google.llm.connect-services",
        "connect-google.machine.client-status",
        "connect-google.machine.install-client",
    }

    for node in (default, create_client, connect_services):
        for edge in node.get("uses_interfaces", []):
            assert not edge["interface"].startswith(
                ("cloud-files.machine.", "g-calendar.machine.", "email-client.machine.")
            )


def test_client_status_declares_every_google_client_path_it_reads() -> None:
    node = load("_rtx/._client_config.py.client-status.blueprint.yaml")

    declared_paths = {
        entry["path"] for entry in node["direct_io"]["reads"]
    }

    assert declared_paths == {
        "$HOME/.config/connect-google/client.json",
        "$HOME/.config/cloud-files/client.json",
        "$HOME/.config/g-calendar/client.json",
    }


def test_default_router_contract() -> None:
    text = body("SKILL.md")
    assert text.startswith("---")
    assert "skill: connect-google" in text
    assert "client-status" in text
    assert "create-client" in text
    assert "connect-services" in text
    assert "connect" in text and "reconnect" in text
    assert "drive" in text and "calendar" in text and "gmail" in text
    assert "do not invoke service machine interfaces" in text
    assert "service skills invoke this skill" in text
    assert "never commit" in text
    assert "dispatcher " not in text
    assert "_rtx" not in text


def test_create_client_route_contract() -> None:
    text = body("llm_interfaces/create-client.md")
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
    assert "_rtx" not in text


def test_connect_services_route_contract() -> None:
    text = body("llm_interfaces/connect-services.md")
    for phrase in (
        "recommend all three",
        "subset",
        "service-owned",
        "hand off",
        "does not list",
        "does not invoke",
    ):
        assert phrase in text
    assert "dispatcher " not in text
    assert "_rtx" not in text
    assert "client_secret" not in text
