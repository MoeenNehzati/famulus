from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[3]
SKILLS = REPO_ROOT / "skills"


def load(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def interface_node(skill: str, canonical_id: str) -> dict[str, object]:
    root = load(SKILLS / skill / "blueprint.yaml")
    _, kind, name = canonical_id.split(".", 2)
    if root.get("schema_version") != 2:
        return root["interfaces"][kind][name]
    locator = next(
        edge for edge in root["interfaces"] if edge["interface"] == canonical_id
    )
    return load(SKILLS / skill / locator["blueprint"]["path"])


def authored_skill(name: str) -> str:
    text = (SKILLS / name / "SKILL.md").read_text(encoding="utf-8")
    for block in ("CONTRACT", "INTERFACES"):
        begin = f"<!-- BEGIN BLUEPRINT {block} -->"
        end = f"<!-- END BLUEPRINT {block} -->"
        if begin in text and end in text:
            prefix, remainder = text.split(begin, 1)
            _, suffix = remainder.split(end, 1)
            text = prefix + suffix
    return text.lower()


def test_email_interfaces_are_not_exposed_to_connect_google() -> None:
    for interface in (
        "accounts-list",
        "accounts-add",
        "accounts-update",
        "accounts-setup-oauth",
        "live-smoke",
    ):
        node = interface_node("email-client", f"email-client.machine.{interface}")
        assert "connect-google" not in node.get("allowed_callers", [])


def test_google_service_llm_interfaces_delegate_to_connect_google() -> None:
    setup_interfaces = {
        "cloud-files": "cloud-files.machine.setup-oauth",
        "g-calendar": "g-calendar.machine.setup-oauth",
        "email-client": "email-client.machine.accounts-setup-oauth",
    }
    for skill, setup_interface in setup_interfaces.items():
        node = interface_node(skill, f"{skill}.llm.default")
        assert {"interface": "connect-google.llm.default", "version": 1} in node[
            "uses_interfaces"
        ]
        assert {"interface": setup_interface, "version": 1} in node["uses_interfaces"]


def test_service_guidance_has_one_google_onboarding_owner() -> None:
    for skill in ("cloud-files", "g-calendar", "email-client"):
        text = authored_skill(skill)
        assert "connect-google.llm.default" in text
        assert "initial google setup" in text
        assert "reauthorization" in text
        assert "create an oauth client" not in text
        assert "create credentials" not in text
        assert "google cloud project" not in text


def test_service_guidance_hands_canonical_client_to_owned_setup_interface() -> None:
    expected = {
        "cloud-files": (
            "cloud-files.machine.setup-oauth",
            "--from-json ~/.config/connect-google/client.json",
        ),
        "g-calendar": (
            "g-calendar.machine.setup-oauth",
            "--from-json ~/.config/connect-google/client.json",
        ),
        "email-client": (
            "email-client.machine.accounts-setup-oauth",
            "--client-config ~/.config/connect-google/client.json",
        ),
    }
    for skill, fragments in expected.items():
        text = authored_skill(skill)
        for fragment in fragments:
            assert fragment in text


def test_installer_does_not_depend_on_connect_google() -> None:
    text = authored_skill("install-assistant-tools")
    assert "phase 2" in text
    assert "connect-google.llm.default" not in text
    assert "connect-google.machine." not in text


def test_service_setup_machine_interfaces_still_exist() -> None:
    expected = {
        "cloud-files": "cloud-files.machine.setup-oauth",
        "g-calendar": "g-calendar.machine.setup-oauth",
        "email-client": "email-client.machine.accounts-setup-oauth",
    }
    for skill, interface in expected.items():
        assert interface_node(skill, interface)
