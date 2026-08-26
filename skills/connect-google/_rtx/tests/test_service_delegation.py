from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Callable

import pytest
import yaml


REPO_ROOT = Path(__file__).resolve().parents[4]
SKILLS = REPO_ROOT / "skills"


BlueprintLoader = Callable[[Path], dict[str, object]]


@pytest.fixture(scope="module")
def blueprint_loader() -> BlueprintLoader:
    """Return independent values while parsing each repository YAML file once."""
    parsed: dict[Path, dict[str, object]] = {}

    def load(path: Path) -> dict[str, object]:
        if path not in parsed:
            parsed[path] = yaml.safe_load(path.read_text(encoding="utf-8"))
        return deepcopy(parsed[path])

    return load


def exported_interface(
    skill: str,
    canonical_id: str,
    load: BlueprintLoader,
) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    module_root = SKILLS / skill
    module = load(module_root / "blueprint.yaml")
    module_id = canonical_id.split(".interface.", 1)[0]
    current_id = skill
    for segment in module_id.split(".")[1:]:
        assert segment in module["children"]
        module_root /= segment
        module = load(module_root / "blueprint.yaml")
        current_id = f"{current_id}.{segment}"
        assert module["id"] == current_id
    assert module["schema_version"] == 6
    source, interface = _resolve_export(
        module_root,
        module,
        canonical_id,
        load,
    )
    return module, source, interface


def _resolve_export(
    module_root: Path,
    module: dict[str, object],
    canonical_id: str,
    load: BlueprintLoader,
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
            load,
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


def test_only_email_file_binder_is_exposed_to_connect_google(
    blueprint_loader: BlueprintLoader,
) -> None:
    for interface in (
        "accounts-list",
        "accounts-add",
        "accounts-update",
        "accounts-setup-oauth",
        "live-smoke",
    ):
        canonical_id = f"email-client._rtx.interface.{interface}"
        root, _, _ = exported_interface("email-client", canonical_id, blueprint_loader)
        access = root["exports"][canonical_id]["access"]
        assert access["allow_all_modules"] is False
        assert "connect-google" not in access["allowed_callers"]
    root, _, _ = exported_interface(
        "email-client",
        "email-client._rtx.interface.accounts-use-google-credential-file",
        blueprint_loader,
    )
    access = root["exports"][
        "email-client._rtx.interface.accounts-use-google-credential-file"
    ]["access"]
    assert set(access["allowed_callers"]) >= {"connect-google", "connect-google._rtx"}


def test_google_service_gateways_delegate_setup_only_to_connect_google(
    blueprint_loader: BlueprintLoader,
) -> None:
    forbidden = ("setup-oauth", "ensure-oauth", "use-google-credential")
    for skill in ("cloud-files", "online-calendar", "email-client"):
        _root, gateway, _ = exported_interface(
            skill, f"{skill}.interface.default", blueprint_loader
        )
        assert {
            "interface": "connect-google.interface.default",
            "version": 1,
        } in gateway["uses_interfaces"]
        interfaces = {edge["interface"] for edge in gateway["uses_interfaces"]}
        assert not any(fragment in interface for fragment in forbidden for interface in interfaces)


def test_coordinator_contract_hands_file_to_service_owners(
    blueprint_loader: BlueprintLoader,
) -> None:
    _, _, connect = exported_interface(
        "connect-google",
        "connect-google._rtx.interface.connect-services",
        blueprint_loader,
    )
    result = next(
        output for output in connect["contract"]["outputs"] if output["id"] == "result"
    )
    assert result["type"]["format"] == {"named": "json"}
    assert "credential_file" in result["description"]

    expected = {
        "cloud-files": "cloud-files._rtx.interface.use-google-credential-file",
        "online-calendar": "online-calendar._rtx.interface.use-google-credential-file",
        "email-client": "email-client._rtx.interface.accounts-use-google-credential-file",
    }
    for skill, interface_id in expected.items():
        _, _, interface = exported_interface(skill, interface_id, blueprint_loader)
        assert "credential-file" in interface["contract"]["arguments"]


def test_email_guidance_routes_shared_credential_and_legacy_fallback() -> None:
    text = authored_skill("email-client")
    paragraphs = text.split("\n\n")
    shared = next(
        paragraph
        for paragraph in paragraphs
        if "connect-google.interface.default" in paragraph
    )
    assert "credential file" in shared
    assert "credential_id" not in text


def test_cloud_guidance_routes_shared_credential_and_legacy_fallback() -> None:
    text = authored_skill("cloud-files")
    paragraphs = text.split("\n\n")
    shared = next(
        paragraph
        for paragraph in paragraphs
        if "connect-google.interface.default" in paragraph
    )
    assert "credential file" in shared
    assert "credential_id" not in text


def test_cloud_file_binder_allows_connect_google(
    blueprint_loader: BlueprintLoader,
) -> None:
    root, _, _ = exported_interface(
        "cloud-files",
        "cloud-files._rtx.interface.use-google-credential-file",
        blueprint_loader,
    )
    access = root["exports"]["cloud-files._rtx.interface.use-google-credential-file"]["access"]
    assert "connect-google._rtx" in access["allowed_callers"]


def test_calendar_gateway_declares_complete_oauth_route_invariants(
    blueprint_loader: BlueprintLoader,
) -> None:
    _, gateway, _ = exported_interface(
        "online-calendar", "online-calendar.interface.default", blueprint_loader
    )

    assert {edge["interface"] for edge in gateway["uses_interfaces"]} == {
        "connect-google.interface.default",
        "online-calendar._rtx.interface.scripts-gcal",
    }


def test_installer_does_not_depend_on_connect_google() -> None:
    text = authored_skill("install-assistant-tools")
    assert "5. explain that `connect-google`" in text
    assert "do not invoke either or make installation success" in text


def test_legacy_setup_exports_still_exist_for_compatibility(
    blueprint_loader: BlueprintLoader,
) -> None:
    expected = {
        "cloud-files": "cloud-files._rtx.interface.setup-oauth",
        "online-calendar": "online-calendar._rtx.interface.setup-oauth",
        "email-client": "email-client._rtx.interface.accounts-setup-oauth",
    }
    for skill, interface in expected.items():
        root, source, contract = exported_interface(skill, interface, blueprint_loader)
        assert contract in source["interfaces"].values()
        assert contract
