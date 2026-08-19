"""Specify the explicit registry and small public Rutter facade."""

from __future__ import annotations

import inspect
from pathlib import Path
from threading import Event, Thread
from typing import Mapping

import pytest

import officina.rutter as rutter_facade
import officina.rutter.runtime as runtime_module
from officina.rutter.model import (
    BaseRutter,
    Charter,
    InputValidatorContract,
    JsonValue,
    RutterDefinitionError,
    RutterStateError,
    State,
    TerminalState,
    ValidationReport,
)
from officina.rutter.runtime import RutterRegistry
from test_support.rutter_fixtures import ExampleRutter


class AlternateRutter(BaseRutter):
    """Provide a second direct identity for registry selection tests."""

    rutter_id = "alternate"
    definition_version = 1
    start_state = "start"

    @staticmethod
    def validate_start(value: Mapping[str, JsonValue]) -> ValidationReport:
        return ValidationReport(valid=value.get("outcome") == "done")

    @staticmethod
    def finish(value: Mapping[str, JsonValue]) -> str:
        del value
        return "complete"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        return {
            "start": State(
                "Finish the alternate undertaking.",
                InputValidatorContract(self.validate_start, ("done",)),
                self.finish,
            ),
            "complete": TerminalState(),
        }


class DuplicateExampleRutter(BaseRutter):
    """Use the example identity from a distinct direct definition."""

    rutter_id = "example"
    definition_version = 1
    start_state = "start"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        raise AssertionError("registry validation must not bind the definition")


class VersionTwoExampleRutter(BaseRutter):
    """Represent a new definition version for persisted mismatch tests."""

    rutter_id = "example"
    definition_version = 2
    start_state = "start"

    def define_states(self) -> Mapping[str, State | TerminalState]:
        raise AssertionError("identity mismatch must fail before graph binding")


@pytest.fixture
def reckoning_root(tmp_path: Path) -> Path:
    """Return one empty registry-confined Reckoning root."""

    return tmp_path / "reckonings"


@pytest.fixture
def registry(reckoning_root: Path) -> RutterRegistry:
    """Return one registry whose public alias differs from stored identity."""

    return RutterRegistry({"friendly-example": ExampleRutter}, reckoning_root)


def test_registry_has_only_create_and_open_construction_routes(
    registry: RutterRegistry,
) -> None:
    """Renaming create or retaining start would break the binding contract."""

    assert tuple(inspect.signature(RutterRegistry).parameters) == (
        "rutters",
        "reckoning_root",
    )
    assert tuple(inspect.signature(registry.create).parameters) == (
        "name",
        "reckoning_path",
        "charter_data",
    )
    assert tuple(inspect.signature(registry.open).parameters) == ("reckoning_path",)
    for removed in ("start", "resume", "inspect", "submit"):
        assert not hasattr(registry, removed)


def test_create_builds_immutable_charter_and_returns_exact_bound_class(
    registry: RutterRegistry,
    reckoning_root: Path,
) -> None:
    """Registry creation owns Charter identity and detaches caller data."""

    options = ["careful"]
    created = registry.create(
        "friendly-example",
        Path("jobs/review.reckoning.json"),
        {"artifact": "draft.md", "options": options},
    )
    options.append("tampered")

    assert type(created) is ExampleRutter
    assert created.charter == Charter(
        "example",
        1,
        {"artifact": "draft.md", "options": ["careful"]},
    )
    assert (reckoning_root / "jobs" / "review.reckoning.json").is_file()


def test_open_selects_only_from_strict_persisted_charter_identity(
    registry: RutterRegistry,
) -> None:
    """Opening needs no public alias and cannot accept a caller-supplied identity."""

    created = registry.create("friendly-example", Path("review.reckoning.json"), {})
    opened = registry.open(Path("review.reckoning.json"))

    assert type(opened) is ExampleRutter
    assert opened.reckoning == created.reckoning
    assert tuple(inspect.signature(registry.open).parameters) == ("reckoning_path",)


def test_registry_open_waits_for_the_reckoning_sidecar_lock(
    registry: RutterRegistry,
    reckoning_root: Path,
) -> None:
    """Registry identity loading participates in the same lock as writers."""

    registry.create("friendly-example", Path("review.reckoning.json"), {})
    path = reckoning_root / "review.reckoning.json"
    writer = runtime_module._ReckoningStore(path.absolute())
    attempted = Event()
    opened = []

    def open_from_registry() -> None:
        attempted.set()
        opened.append(registry.open(Path("review.reckoning.json")))

    with writer.transaction():
        thread = Thread(target=open_from_registry)
        thread.start()
        assert attempted.wait(timeout=1)
        thread.join(timeout=0.1)
        assert not opened
    thread.join(timeout=1)

    assert len(opened) == 1
    assert type(opened[0]) is ExampleRutter


def test_registry_copies_registration_mapping(
    reckoning_root: Path,
) -> None:
    """Later caller mutation cannot install an ambient registry binding."""

    definitions: dict[str, type[BaseRutter]] = {"example": ExampleRutter}
    registry = RutterRegistry(definitions, reckoning_root)
    definitions["alternate"] = AlternateRutter

    with pytest.raises(RutterStateError, match="unknown Rutter"):
        registry.create("alternate", Path("alternate.reckoning.json"), {})


@pytest.mark.parametrize(
    "registrant",
    (
        lambda path, charter: ExampleRutter.create(path, charter),
        object.__new__(ExampleRutter),
        object,
    ),
)
def test_registry_rejects_factories_instances_and_unrelated_classes(
    reckoning_root: Path,
    registrant: object,
) -> None:
    """Any non-definition registrant would reintroduce hidden construction authority."""

    with pytest.raises(RutterDefinitionError, match="direct BaseRutter subclass"):
        RutterRegistry({"invalid": registrant}, reckoning_root)  # type: ignore[dict-item]


def test_registry_rejects_indirect_subclasses(
    reckoning_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Runtime validates direct ownership even if a class bypasses the engine hook."""

    monkeypatch.setattr(
        BaseRutter,
        "__init_subclass__",
        classmethod(lambda cls, **kwargs: None),
    )

    class IndirectRutter(ExampleRutter):
        pass

    with pytest.raises(RutterDefinitionError, match="direct BaseRutter subclass"):
        RutterRegistry({"indirect": IndirectRutter}, reckoning_root)


def test_registry_rejects_duplicate_persisted_identities(
    reckoning_root: Path,
) -> None:
    """Two public names cannot compete for the same Reckoning identity."""

    with pytest.raises(RutterDefinitionError, match="duplicate rutter_id 'example'"):
        RutterRegistry(
            {"first": ExampleRutter, "second": DuplicateExampleRutter},
            reckoning_root,
        )


@pytest.mark.parametrize(
    ("attribute", "replacement"),
    (("rutter_id", "changed-example"), ("definition_version", 2)),
)
def test_registry_rejects_identity_or_version_drift_after_registration(
    registry: RutterRegistry,
    monkeypatch: pytest.MonkeyPatch,
    attribute: str,
    replacement: object,
) -> None:
    """Mutable class metadata cannot alter a validated registry binding."""

    monkeypatch.setattr(ExampleRutter, attribute, replacement)

    with pytest.raises(RutterDefinitionError, match="changed after registration"):
        registry.create("friendly-example", Path("review.reckoning.json"), {})


def test_open_rejects_definition_version_mismatch(
    reckoning_root: Path,
) -> None:
    """A stored Charter version cannot be rebound to a different definition."""

    RutterRegistry({"example": ExampleRutter}, reckoning_root).create(
        "example", Path("review.reckoning.json"), {}
    )
    changed = RutterRegistry({"example": VersionTwoExampleRutter}, reckoning_root)

    with pytest.raises(RutterStateError, match="definition_version"):
        changed.open(Path("review.reckoning.json"))


@pytest.mark.parametrize(
    "reckoning_path",
    (
        Path("/absolute.reckoning.json"),
        Path("../escape.reckoning.json"),
        Path("nested/../escape.reckoning.json"),
    ),
)
def test_registry_rejects_absolute_and_traversal_paths(
    registry: RutterRegistry,
    reckoning_path: Path,
) -> None:
    """Every registry operation remains a lexical descendant of its configured root."""

    with pytest.raises(RutterDefinitionError, match="relative path"):
        registry.create("friendly-example", reckoning_path, {})
    with pytest.raises(RutterDefinitionError, match="relative path"):
        registry.open(reckoning_path)


def test_registry_rejects_names_outside_the_reckoning_suffix_contract(
    registry: RutterRegistry,
) -> None:
    """Registry operations cannot address a Reckoning lock as authority."""

    sidecar = Path("jobs/paper.reckoning.json.lock")

    with pytest.raises(RutterDefinitionError, match=r"\.reckoning\.json"):
        registry.create("friendly-example", sidecar, {})
    with pytest.raises(RutterDefinitionError, match=r"\.reckoning\.json"):
        registry.open(sidecar)


def test_create_and_open_reject_missing_bindings(
    registry: RutterRegistry,
    reckoning_root: Path,
) -> None:
    """Unknown public names and persisted identities fail closed."""

    with pytest.raises(RutterStateError, match="unknown Rutter"):
        registry.create("missing", Path("missing.reckoning.json"), {})

    AlternateRutter.create(
        reckoning_root / "alternate.reckoning.json",
        Charter("alternate", 1, {}),
    )
    with pytest.raises(RutterStateError, match="unknown Rutter identity"):
        registry.open(Path("alternate.reckoning.json"))


def test_open_rejects_non_reckoning_bytes(
    registry: RutterRegistry,
    reckoning_root: Path,
) -> None:
    """Registry bootstrap identity is accepted only from strict Reckoning decoding."""

    reckoning_root.mkdir(parents=True)
    (reckoning_root / "corrupt.reckoning.json").write_text(
        '{"charter":{"rutter_id":"example"}}\n', encoding="utf-8"
    )

    with pytest.raises(RutterStateError):
        registry.open(Path("corrupt.reckoning.json"))


def test_facade_exports_only_authoring_and_binding_values() -> None:
    """Codec, lock, effect-recovery, and retired compatibility symbols stay private."""

    assert rutter_facade.__all__ == (
        "BaseRutter",
        "Charter",
        "EffectPolicy",
        "Fix",
        "InputValidatorContract",
        "JsonValue",
        "Reckoning",
        "RutterDefinitionError",
        "RutterRegistry",
        "RutterStateError",
        "RutterValidationError",
        "State",
        "TerminalState",
        "ValidationIssue",
        "ValidationReport",
    )
    assert runtime_module.__all__ == ("RutterRegistry",)
    for removed in (
        "RutterFactory",
        "RutterCreator",
        "_FileFixStore",
        "FixStore",
        "give_instructions",
        "validate_result",
        "update",
    ):
        assert not hasattr(runtime_module, removed)
