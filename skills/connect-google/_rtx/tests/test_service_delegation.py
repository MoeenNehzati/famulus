from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
SKILLS = REPO_ROOT / "skills"


def load(path: Path) -> dict[str, object]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def exported_interface(
    skill: str, canonical_id: str
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    module_root = SKILLS / skill
    root = load(module_root / "blueprint.yaml")
    assert root["schema_version"] == 5
    source, interface = _resolve_export(
        module_root,
        root,
        canonical_id,
    )
    return root, source, interface


def _resolve_export(
    module_root: Path,
    module: dict[str, object],
    canonical_id: str,
) -> tuple[dict[str, object], dict[str, object]]:
    export = module["exports"][canonical_id]
    facade = export.get("facade_interface")
    if facade is not None:
        child_id = facade["interface"].split(".interface.", 1)[0]
        child_marker = module_root / module["children"][child_id]["path"]
        child = load(child_marker)
        return _resolve_export(
            child_marker.parent,
            child,
            facade["interface"],
        )
    source_interface = export["source_interface"]
    source_id, _, _ = source_interface.rpartition(".interface.")
    locator = module["sources"][source_id]["blueprint"]
    assert locator["base"] == "module-root"
    source = load(module_root / locator["path"])
    assert source["id"] == source_id
    interface = source["interfaces"][source_interface]
    return source, interface


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
        canonical_id = f"email-client.interface.{interface}"
        root, _, _ = exported_interface("email-client", canonical_id)
        access = root["exports"][canonical_id]["access"]
        assert access["allow_all_modules"] is False
        assert "connect-google" not in access["allowed_callers"]


def test_google_service_gateways_delegate_to_connect_google() -> None:
    setup_interfaces = {
        "cloud-files": "cloud-files.interface.setup-oauth",
        "g-calendar": "g-calendar.interface.setup-oauth",
        "email-client": "email-client.interface.accounts-setup-oauth",
    }
    for skill, setup_interface in setup_interfaces.items():
        root, gateway, _ = exported_interface(skill, f"{skill}.interface.default")
        assert {
            "interface": "connect-google.interface.default",
            "version": 1,
        } in gateway["uses_interfaces"]
        assert {
            "interface": setup_interface,
            "version": 1,
        } in gateway["uses_interfaces"]


def test_service_guidance_has_one_google_onboarding_owner() -> None:
    for skill in ("cloud-files", "g-calendar", "email-client"):
        text = authored_skill(skill)
        assert "connect-google.interface.default" in text
        assert "initial google setup" in text
        assert "reauthorization" in text
        assert "create an oauth client" not in text
        assert "create credentials" not in text
        assert "google cloud project" not in text


def test_service_guidance_hands_canonical_client_to_owned_setup_interface() -> None:
    expected = {
        "cloud-files": (
            "cloud-files.interface.setup-oauth",
            "--from-json ~/.config/connect-google/client.json",
        ),
        "g-calendar": (
            "g-calendar.interface.setup-oauth",
            "--from-json ~/.config/connect-google/client.json",
        ),
        "email-client": (
            "email-client.interface.accounts-setup-oauth",
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
    assert "connect-google." not in text


def test_service_setup_exports_still_exist() -> None:
    expected = {
        "cloud-files": "cloud-files.interface.setup-oauth",
        "g-calendar": "g-calendar.interface.setup-oauth",
        "email-client": "email-client.interface.accounts-setup-oauth",
    }
    for skill, interface in expected.items():
        root, source, contract = exported_interface(skill, interface)
        assert contract in source["interfaces"].values()
        assert contract
