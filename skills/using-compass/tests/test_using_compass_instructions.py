"""Contract tests for operating Voyages through one process binding."""

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


def test_authored_body_assigns_one_agent_to_each_dispensed_voyage() -> None:
    """One controller agent operating every ID would violate Voyage ownership."""

    text = _normalized_body()

    assert "one invoker-provided authorized `VoyageDispenser` process binding" in text
    assert "invoke `help`" in text
    assert "Invoke the appropriately scoped `list`" in text
    assert "same prefix for both `list` and any required `initiate`" in text
    assert "assign exactly one agent to each returned `voyage_id`" in text
    assert "assigned `voyage_id`" in text
    assert "must not share or switch" in text
    assert "do not start any voyage agent until every" in text.lower()
    assert "wait for every Voyage agent" in text
    assert "public-interface gap" in text
    assert "invokes `release`" in text
    assert "explicit reason to preserve" in text
    assert "never releases a ready, faulted, uncertain" in text


def test_authored_body_does_not_expect_a_python_object_or_runtime_help() -> None:
    """Markdown cannot receive a live Voyage or discover methods through help()."""

    text = _authored_body()
    forbidden = ("voyage.help()", "python-object", "live Python", "`Voyage` supplied")

    assert not [token for token in forbidden if token in text]


def test_blueprint_consumes_the_voyage_dispenser_contract() -> None:
    """A Python-object dependency would preserve the missing prompt transport."""

    root = yaml.safe_load(BLUEPRINT_PATH.read_text(encoding="utf-8"))
    gateway = yaml.safe_load(GATEWAY_BLUEPRINT_PATH.read_text(encoding="utf-8"))
    interface = gateway["interfaces"][
        "using-compass.source.gateway.interface.default"
    ]
    contract = interface["contract"]

    assert root["schema_version"] == gateway["schema_version"] == 6
    assert root["version"] == gateway["version"] == interface["version"] == 11
    assert gateway["uses_interfaces"] == [
        {"interface": "rutter.interface.dispenser", "version": 4}
    ]
    assert interface["uses_interfaces"] == [
        {"interface": "rutter.interface.dispenser", "version": 4}
    ]
    assert set(contract["arguments"]) == {"request", "binding", "run-prefix"}
    binding = contract["arguments"]["binding"]
    assert binding["required"] is True
    assert "one authorized" in binding["description"]
    assert "VoyageDispenser" in binding["description"]
    assert "process" in binding["description"]
    assert root["exports"] == {
        "using-compass.interface.default": {
            "source_interface": "using-compass.source.gateway.interface.default",
            "access": {"allow_all_modules": True, "allowed_callers": []},
        }
    }
    assert interface["usage"] == (
        "request=<Use compass on rutter-name>; "
        "binding=<one authorized VoyageDispenser process binding>; "
        "run-prefix=<optional isolated run name>"
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
    bound = [entry for entry in entries if entry["id"] == "voyage-dispenser"]
    assert len(bound) == 1
    assert bound[0]["system"] == "rutter.interface.dispenser"
    assert bound[0]["formats"] == ["process-interface"]


def test_authored_body_uses_binding_without_consuming_its_own_export() -> None:
    """A skill's public self-interface is not authority it can consume."""

    text = _authored_body()

    assert "using-compass.interface.default" not in text
    assert "invoker-provided authorized `VoyageDispenser` process binding" in text
