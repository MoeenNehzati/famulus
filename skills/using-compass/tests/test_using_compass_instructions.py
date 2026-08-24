"""Contract tests for the self-describing Voyage bootstrap."""

from pathlib import Path

import yaml


SKILL_PATH = Path(__file__).resolve().parents[1] / "SKILL.md"
BLUEPRINT_PATH = SKILL_PATH.parent / "blueprint.yaml"
GATEWAY_BLUEPRINT_PATH = SKILL_PATH.parent / "blueprints" / "gateway.yaml"


def _authored_body() -> str:
    return SKILL_PATH.read_text(encoding="utf-8").split(
        "<!-- END BLUEPRINT INTERFACES -->", maxsplit=1
    )[1]


def _normalized_body() -> str:
    return " ".join(_authored_body().split())


def test_authored_body_bootstraps_only_from_voyage_help() -> None:
    """Handwritten operation details would recouple Compass to one API revision."""

    text = _normalized_body()

    assert "one invoker-provided authorized `Voyage`" in text
    assert "`voyage.help()`" in text
    assert "advertised methods" in text
    assert "public name" in text
    assert "bound signature" in text
    assert "nonempty docstring" in text
    assert "public-interface gap" in text


def test_authored_body_does_not_duplicate_voyage_operating_contract() -> None:
    """Runtime method docs must remain the single operating-contract source."""

    text = _authored_body()
    forbidden = (
        "get_status",
        "validate",
        "next",
        "VoyageStatus",
        "Message",
        "response",
        "ready",
        "terminal",
        "fault",
        "uncertain",
        "continue_",
        "dry_run",
        "revision",
        "outcome",
        "evidence",
    )

    assert not [token for token in forbidden if token in text]


def test_blueprint_consumes_v5_self_describing_bound_operations() -> None:
    """Generated guidance must authorize help without duplicating operation details."""

    root = yaml.safe_load(BLUEPRINT_PATH.read_text(encoding="utf-8"))
    gateway = yaml.safe_load(GATEWAY_BLUEPRINT_PATH.read_text(encoding="utf-8"))
    interface = gateway["interfaces"][
        "using-compass.source.gateway.interface.default"
    ]
    contract = interface["contract"]

    assert root["schema_version"] == gateway["schema_version"] == 6
    assert root["version"] == gateway["version"] == interface["version"] == 7
    assert gateway["uses_interfaces"] == [
        {"interface": "rutter.interface.bound-operations", "version": 5}
    ]
    assert interface["uses_interfaces"] == [
        {"interface": "rutter.interface.bound-operations", "version": 5}
    ]
    assert set(contract["arguments"]) == {"request", "binding"}
    binding = contract["arguments"]["binding"]
    assert binding["required"] is True
    assert "one authorized" in binding["description"]
    assert "Voyage" in binding["description"]
    assert "self-describing" in binding["description"]
    assert root["exports"] == {
        "using-compass.interface.default": {
            "source_interface": "using-compass.source.gateway.interface.default",
            "access": {"allow_all_modules": True, "allowed_callers": []},
        }
    }
    assert interface["usage"] == (
        "request=<Use compass on rutter-name>; "
        "binding=<one authorized Voyage>"
    )
    outcomes = {outcome["id"]: outcome for outcome in contract["outcomes"]}
    assert set(outcomes) == {"completed", "interface-gap"}
    assert outcomes["interface-gap"]["effects"] == []
    effects = contract["execution"]["effects"]
    assert [effect["id"] for effect in effects] == ["voyage-operation"]
    assert effects[0]["may_occur_in_outcomes"] == ["completed"]
    direct_io = contract["direct_io"]
    entries = [entry for group in ("reads", "writes") for entry in direct_io[group]]
    assert not [entry for entry in entries if entry["medium"] == "local-filesystem"]
    assert not [entry for entry in entries if "path" in entry]
    bound = [entry for entry in entries if entry["id"] == "voyage"]
    assert len(bound) == 1
    assert bound[0]["system"] == "rutter.interface.bound-operations"
    assert "self-described" in bound[0]["content"]


def test_authored_body_uses_binding_without_consuming_its_own_export() -> None:
    """A skill's public self-interface is not authority it can consume."""

    text = _authored_body()

    assert "using-compass.interface.default" not in text
    assert "invoker-provided authorized `Voyage`" in text
