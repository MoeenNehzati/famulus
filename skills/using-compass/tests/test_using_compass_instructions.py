"""Contract tests for the source-free bound-Rutter operating guide."""

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


def test_loop_settles_callable_work_before_requesting_instruction() -> None:
    """Calling get_instruction first would expose callable work to the LLM."""

    text = _normalized_body()
    settle = "`advance(continue_=True)`"
    instruction = "`get_instruction()`"

    assert "one invoker-provided bound Rutter instance" in text
    assert text.index(settle) < text.index(instruction)
    assert "input_required" in text
    assert "Do not call `get_instruction()` before this settling attempt" in text
    assert "consume its returned successor" in text


def test_loop_requires_exact_revision_outcome_evidence_envelope() -> None:
    """Bare outcomes would discard the bound revision and evidence object contract."""

    text = _authored_body()
    normalized = " ".join(text.split())

    assert '"revision": <displayed integer>' in text
    assert '"outcome": "<declared outcome or unexpected>"' in text
    assert '"evidence": {<finite JSON object>}' in text
    assert "exactly these three fields" in normalized
    assert "displayed revision" in normalized


def test_loop_validates_before_normal_continuation() -> None:
    """Using one-edge movement would bypass normal callable continuation."""

    text = _normalized_body()
    validate = "`validate(result)`"
    advance = "`advance(result, continue_=True)`"

    assert text.index(validate) < text.index(advance)
    assert "If validation is invalid, do not advance" in text
    assert "Never use `continue_=False` in the Compass loop" in text
    continuation = text.index("Call `advance(result, continue_=True)`")
    classify = text.index("classify the returned successor", continuation)
    next_instruction = text.index("call `get_instruction()`", classify)
    assert continuation < classify < next_instruction
    assert "repeat the settle step" not in text


def test_dry_run_is_never_effect_authorization() -> None:
    """A preview cannot authorize or execute callable work or external effects."""

    text = _normalized_body()

    assert "`dry_run=True` only previews one supplied result edge" in text
    assert "does not invoke instructions or authorize effects" in text
    assert "Never use dry run as permission to perform work" in text


def test_successful_advance_classifies_each_stopped_authority_before_another_call() -> None:
    """A successful continuation into stopped authority cannot be advanced again."""

    text = _normalized_body()
    classify = text.index("After every successful `advance(...)`")
    complete = text.index('`fix.lifecycle == "complete"`', classify)
    faulted = text.index('`fix.lifecycle == "faulted"`', classify)
    uncertain = text.index('`fix.effect.disposition == "uncertain"`', classify)
    active = text.index('`fix.lifecycle == "active"`', classify)
    next_call = text.index("Only the active branch permits another public call", classify)

    assert classify < complete < faulted < uncertain < active < next_call
    assert "call neither `advance()` nor `get_instruction()` again" in text
    assert "report the returned successor and terminal status, then stop" in text
    assert "report the public fault diagnostics, then stop" in text
    assert "stop for manual reconciliation" in text


def test_stopped_authority_exception_is_classified_once_without_retry() -> None:
    """A stopped-state exception must not trigger the same prohibited call again."""

    text = _normalized_body()
    exception = text.index("If `advance(...)` raises `RutterStateError`")
    inspect = text.index("inspect the public `fix` once", exception)
    stopped = text.index("apply the same complete, faulted, or uncertain branch", inspect)
    gap = text.index("report a public-interface gap and stop", stopped)
    prohibited = text.index("Do not retry `advance()` or call `get_instruction()`", gap)

    assert exception < inspect < stopped < gap < prohibited


def test_structured_callable_and_effect_statuses_classify_advance_results() -> None:
    """Every authorized structured continuation consumes its returned successor."""

    text = _normalized_body()
    for status in ("callable", "effectful_callable", "pending_effect"):
        branch = text.index(f"For `{status}`")
        advance = text.index("`advance(continue_=True)`", branch)
        classify = text.index("classify its returned successor", advance)
        assert branch < advance < classify

    for status in ("uncertain_effect", "terminal", "fault"):
        assert f"For `{status}`" in text
    assert "authorized_operation" in text
    assert "No public recovery transition is authorized" in text
    assert "Do not infer a transition" in text


def test_mismatch_uses_exact_unexpected_evidence_without_source_search() -> None:
    """Undeclared observations become explicit diagnostics rather than invented routes."""

    text = _authored_body()
    for field in (
        "observed",
        "conflict",
        "why_no_outcome_fits",
        "uncertainty",
    ):
        assert f'"{field}"' in text
    normalized = _normalized_body()
    assert "all four values must be non-empty strings" in normalized
    assert "Do not inspect Rutter source" in normalized
    assert "registry, codec, lock, or storage internals" in normalized
    assert "report a public-interface gap and stop" in normalized


def test_blueprint_binds_one_instance_through_the_public_rutter_export() -> None:
    """The generated interface must be sufficient without registry or path knowledge."""

    root = yaml.safe_load(BLUEPRINT_PATH.read_text(encoding="utf-8"))
    gateway = yaml.safe_load(GATEWAY_BLUEPRINT_PATH.read_text(encoding="utf-8"))
    interface = gateway["interfaces"][
        "using-compass.source.gateway.interface.default"
    ]
    contract = interface["contract"]

    assert root["schema_version"] == gateway["schema_version"] == 6
    assert root["version"] == gateway["version"] == interface["version"] == 5
    assert gateway["uses_interfaces"] == [
        {"interface": "rutter.interface.bound-operations", "version": 1}
    ]
    assert set(contract["arguments"]) == {"request", "binding"}
    binding = contract["arguments"]["binding"]
    assert binding["required"] is True
    assert "one authorized bound BaseRutter instance" in binding["description"]
    for forbidden in ("RutterRegistry", "rutter_name", "fix_path", "codec", "lock"):
        assert forbidden not in binding["description"]
    assert root["exports"] == {
        "using-compass.interface.default": {
            "source_interface": "using-compass.source.gateway.interface.default",
            "access": {"allow_all_modules": True, "allowed_callers": []},
        }
    }
    assert interface["usage"] == (
        "request=<Use compass on rutter-name>; "
        "binding=<one authorized bound BaseRutter instance>"
    )
    assert contract["execution"]["state_effect"] == "mutating"
    effects = contract["execution"]["effects"]
    assert [effect["id"] for effect in effects] == ["bound-rutter-operation"]
    assert effects[0]["direct_io_ref"] == "bound-rutter"
    assert effects[0]["may_occur_in_outcomes"] == [
        "advanced",
        "terminal",
        "faulted",
        "uncertain",
    ]
    outcomes = {outcome["id"]: outcome for outcome in contract["outcomes"]}
    assert outcomes["interface-gap"]["effects"] == []
    direct_io = contract["direct_io"]
    entries = [entry for group in ("reads", "writes") for entry in direct_io[group]]
    assert not [entry for entry in entries if entry["medium"] == "local-filesystem"]
    assert not [entry for entry in entries if "path" in entry]
    bound = [entry for entry in entries if entry["id"] == "bound-rutter"]
    assert len(bound) == 1
    assert bound[0]["medium"] == "local-system"
    assert bound[0]["system"] == "rutter.interface.bound-operations"
    assert "encapsulated" in bound[0]["content"]


def test_authored_body_uses_supplied_binding_without_consuming_its_own_export() -> None:
    """A skill's public self-interface is not authority it can consume."""

    text = _authored_body()

    assert "using-compass.interface.default" not in text
    assert "invoker-provided bound Rutter instance" in text


def test_authored_body_contains_no_legacy_runtime_vocabulary() -> None:
    """The replacement guide cannot retain the former Fix operating surface."""

    text = _authored_body()
    forbidden = (
        "registry.start",
        "registry.open",
        "give_instructions",
        "validate_result",
        "update(result)",
        "rutter.fix",
        "Fix transaction",
        "Fix file",
    )
    assert not [token for token in forbidden if token in text]
