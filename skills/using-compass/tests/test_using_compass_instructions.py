"""Contract tests for the thin four-operation Compass guide."""

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


def test_loop_settles_automatic_work_before_requesting_message() -> None:
    """Reading first would expose engine-owned Python or child work to Compass."""

    text = _normalized_body()
    settle = "`next(continue_=True)`"
    instruction = "`get_instruction()`"

    assert "one invoker-provided bound Rutter instance" in text
    assert text.index(settle) < text.index(instruction)
    assert "settle automatic work" in text
    assert "deepest active node" in text
    assert "Do not call `get_instruction()` before this settling call" in text


def test_message_has_exact_instruction_and_data_parts() -> None:
    """A flat or three-part instruction would violate the public Message boundary."""

    text = _authored_body()
    normalized = " ".join(text.split())

    assert "exactly two top-level parts" in normalized
    assert '"instructions"' in text
    assert '"data"' in text
    for field in (
        "`instructions.text`",
        "`instructions.answer`",
        "`data.state`",
        "`data.payload`",
    ):
        assert field in text
    assert "engine-owned" in normalized


def test_llm_response_is_validated_then_passed_to_next() -> None:
    """Accepting an LLM response anywhere else would split advancing authority."""

    text = _authored_body()
    normalized = " ".join(text.split())
    validate = "`validate(response)`"
    advance = "`next(response, continue_=True)`"

    assert '"revision"' in text
    assert '"outcome"' in text
    assert '"evidence"' in text
    assert normalized.index(validate) < normalized.index(advance)
    assert "read-only" in normalized
    assert "leaves the current node unchanged" in normalized
    assert "repair only from the returned public issues" in normalized


def test_continuation_classifies_each_public_stopping_condition() -> None:
    """A stopped node must never be mistaken for authority to continue."""

    text = _normalized_body()
    ready = text.index("`ready`")
    terminal = text.index("`terminal`", ready)
    fault = text.index("`fault`", terminal)
    uncertain = text.index("`uncertain`", fault)

    assert ready < terminal < fault < uncertain
    assert "report the terminal result and stop" in text
    assert "report the public fault and stop" in text
    assert "stop for manual reconciliation" in text
    assert "Only `ready` permits `get_instruction()`" in text


def test_intermediate_continuation_is_read_from_durable_history() -> None:
    """The final NodeView cannot stand in for automatically traversed nodes."""

    text = _normalized_body()

    assert "returns only the final entered `NodeView`" in text
    assert "durable history records every intermediate traversal" in text
    assert "Do not reconstruct that path from conversation history" in text


def test_compass_leaves_engine_owned_work_to_rutter() -> None:
    """Compass must not regain Python execution or nesting authority in prose."""

    text = _authored_body()
    normalized = " ".join(text.split())

    assert "Rutter owns automatic Python work, hooks, diagnostics, nesting" in normalized
    assert "never execute an internal instruction" in normalized
    assert "never manipulate child traversal" in normalized
    forbidden = (
        "advance(",
        "fix.lifecycle",
        "fix.effect",
        "PythonInstruction",
        "child stack",
        "Charter",
        "Fix",
    )
    assert not [token for token in forbidden if token in text]


def test_current_node_is_observed_only_through_public_operation() -> None:
    """Resume diagnostics must use the active leaf view, not internal state."""

    text = _normalized_body()

    assert "`get_current_node()`" in text
    assert "immutable active-leaf view" in text
    assert "initial response-free settling call" in text
    assert "No later validation failure grants instruction authority" in text
    assert "public-interface gap" in text
    assert "registry" not in text.lower()
    assert "storage" not in text.lower()


def test_response_required_boundary_is_not_invalid_input() -> None:
    """A response-free Prompt boundary raises instead of returning validation issues."""

    text = _normalized_body()

    assert "`response-required`" in text
    assert "`RutterValidationError`" in text
    assert "`Prompt response is required`" in text
    assert "does not return a `ValidationReport`" in text
    assert "not `invalid-input`" in text
    boundary = text.split("`response-required` boundary", maxsplit=1)[1]
    assert boundary.index("`get_current_node()`") < boundary.index(
        "`get_instruction()`"
    )
    assert "perform the LLM instruction" in boundary


def test_only_unrecognized_conditions_are_interface_gaps() -> None:
    """Recognized stopping boundaries must not contradict the fallback wording."""

    text = _normalized_body()

    assert "Any unrecognized condition is a public-interface gap" in text
    assert "Any other condition is a public-interface gap" not in text


def test_dry_run_is_preview_only_and_outside_normal_loop() -> None:
    """A parent-edge preview cannot authorize work or replace continuation."""

    text = _normalized_body()

    assert "`next(response, dry_run=True)`" in text
    assert "immediate parent-edge preview" in text
    assert "never performs work or authorizes work" in text
    assert "Do not use it in the normal Compass loop" in text


def test_blueprint_consumes_v3_bound_operations_through_complete_contract() -> None:
    """Generated guidance needs the complete v3 binding without private authority."""

    root = yaml.safe_load(BLUEPRINT_PATH.read_text(encoding="utf-8"))
    gateway = yaml.safe_load(GATEWAY_BLUEPRINT_PATH.read_text(encoding="utf-8"))
    interface = gateway["interfaces"][
        "using-compass.source.gateway.interface.default"
    ]
    contract = interface["contract"]

    assert root["schema_version"] == gateway["schema_version"] == 6
    assert root["version"] == gateway["version"] == interface["version"] == 5
    assert gateway["uses_interfaces"] == [
        {"interface": "rutter.interface.bound-operations", "version": 3}
    ]
    assert interface["uses_interfaces"] == [
        {"interface": "rutter.interface.bound-operations", "version": 3}
    ]
    assert set(contract["arguments"]) == {"request", "binding"}
    binding = contract["arguments"]["binding"]
    assert binding["required"] is True
    assert "one authorized bound Rutter instance" in binding["description"]
    assert "four public operations" in binding["description"]
    assert root["exports"] == {
        "using-compass.interface.default": {
            "source_interface": "using-compass.source.gateway.interface.default",
            "access": {"allow_all_modules": True, "allowed_callers": []},
        }
    }
    assert interface["usage"] == (
        "request=<Use compass on rutter-name>; "
        "binding=<one authorized bound Rutter instance>"
    )
    outcomes = {outcome["id"]: outcome for outcome in contract["outcomes"]}
    assert set(outcomes) == {
        "ready",
        "terminal",
        "faulted",
        "uncertain",
        "response-required",
        "validation-failed",
        "interface-gap",
    }
    response_required = outcomes["response-required"]
    assert response_required["class"] == "refusal"
    assert response_required["effects"] == []
    assert "Prompt response is required" in response_required["caller_action"]
    assert "current NodeView" in response_required["caller_action"]
    assert "get_instruction" in response_required["caller_action"]
    assert "perform the LLM instruction" in response_required["caller_action"]
    assert "does not return a ValidationReport" in response_required["caller_action"]
    assert outcomes["validation-failed"]["effects"] == []
    assert outcomes["interface-gap"]["effects"] == []
    effects = contract["execution"]["effects"]
    assert [effect["id"] for effect in effects] == ["bound-rutter-operation"]
    assert effects[0]["may_occur_in_outcomes"] == [
        "ready",
        "terminal",
        "faulted",
        "uncertain",
    ]
    direct_io = contract["direct_io"]
    entries = [entry for group in ("reads", "writes") for entry in direct_io[group]]
    assert not [entry for entry in entries if entry["medium"] == "local-filesystem"]
    assert not [entry for entry in entries if "path" in entry]
    bound = [entry for entry in entries if entry["id"] == "bound-rutter"]
    assert len(bound) == 1
    assert bound[0]["system"] == "rutter.interface.bound-operations"
    assert "get_instruction, validate, next, and get_current_node" in bound[0][
        "content"
    ]


def test_authored_body_uses_binding_without_consuming_its_own_export() -> None:
    """A skill's public self-interface is not authority it can consume."""

    text = _authored_body()

    assert "using-compass.interface.default" not in text
    assert "invoker-provided bound Rutter instance" in text
