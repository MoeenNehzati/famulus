"""Specify pure definition binding and the registry-owned voyage boundary."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path
from threading import Event, Thread
from typing import Mapping

import pytest

import officina.rutter.engine as engine_module
import officina.rutter.runtime as runtime_module
from officina.rutter.engine import Voyage
from officina.rutter.model import (
    MachineStep,
    ActiveChild,
    ActiveRun,
    AnswerSpec,
    SubRutter,
    SubRutterRecord,
    Charter,
    CompletedRun,
    Terminal,
    TerminalRecord,
    EnteredEvolution,
    KnownFault,
    Message,
    LLMStep,
    Reckoning,
    Response,
    VoyageResult,
    Rutter,
    RutterDefinitionError,
    RutterStateError,
    Turn,
)
from officina.rutter.runtime import RutterRegistry
from officina.rutter.storage import ReckoningStore
from test_support.rutter_fixtures import (
    AttachedChildRutter,
    DirectChildRutter,
    DiscoveryRootRutter,
    ExampleRutter,
    GrandchildRutter,
    child_charter,
    example_message,
    transition_hook_probe,
)


def _definition(
    states: Mapping[str, object],
    *,
    rutter_id: object = "probe",
    definition_version: object = 1,
    initial_evolution_id: object = "start",
    allow_multiple_hooks_per_transition: object = False,
    transition_hooks: tuple[object, ...] = (),
) -> type[Rutter]:
    class Definition(Rutter):
        def define_evolutions(self):
            return states

        def define_transition_hooks(self):
            return transition_hooks

    Definition.rutter_id = rutter_id  # type: ignore[assignment]
    Definition.definition_version = definition_version  # type: ignore[assignment]
    Definition.initial_evolution_id = initial_evolution_id  # type: ignore[assignment]
    Definition.allow_multiple_hooks_per_transition = allow_multiple_hooks_per_transition  # type: ignore[assignment]
    return Definition


def _done_states() -> Mapping[str, object]:
    return {"start": Terminal(VoyageResult("complete", {}))}


def _prompt_message(
    entry_id: str,
    state_id: str = "report",
    revision: int = 1,
) -> Message:
    return Message(
        instructions={"text": "Report.", "answer": {"reported": {}}},
        data={
            "evolution": {
                "id": state_id,
                "entry_id": entry_id,
                "revision": revision,
            },
            "payload": {"chunk": "A"},
        },
    )


@pytest.fixture
def reckoning_root(tmp_path: Path) -> Path:
    return tmp_path / "reckonings"


@pytest.fixture
def registry(reckoning_root: Path) -> RutterRegistry:
    return RutterRegistry({"friendly-example": ExampleRutter}, reckoning_root)


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    (
        ("rutter_id", "", "Rutter ID"),
        ("rutter_id", "bad id", "Rutter ID"),
        ("definition_version", 0, "definition_version"),
        ("definition_version", True, "definition_version"),
        ("initial_evolution_id", "missing", "initial_evolution_id"),
        ("allow_multiple_hooks_per_transition", 1, "allow_multiple_hooks_per_transition"),
    ),
)
def test_binding_rejects_invalid_definition_metadata(
    reckoning_root: Path,
    attribute: str,
    value: object,
    message: str,
) -> None:
    values = {
        "rutter_id": "probe",
        "definition_version": 1,
        "initial_evolution_id": "start",
        "allow_multiple_hooks_per_transition": False,
    }
    values[attribute] = value
    definition = _definition(_done_states(), **values)

    with pytest.raises(RutterDefinitionError, match=message):
        RutterRegistry({"probe": definition}, reckoning_root)


class _DuplicateStateMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        if key != "start":
            raise KeyError(key)
        return Terminal(VoyageResult("complete", {}))

    def __iter__(self) -> Iterator[str]:
        return iter(("start", "start"))

    def __len__(self) -> int:
        return 2


def test_binding_rejects_duplicate_evolution_ids(reckoning_root: Path) -> None:
    with pytest.raises(RutterDefinitionError, match="duplicate evolution ID"):
        RutterRegistry(
            {"probe": _definition(_DuplicateStateMapping())},
            reckoning_root,
        )


def test_binding_rejects_duplicate_case_maker_ids(reckoning_root: Path) -> None:
    makers = (
        transition_hook_probe("same", DirectChildRutter, child_charter),
        transition_hook_probe("same", AttachedChildRutter, child_charter),
    )

    with pytest.raises(RutterDefinitionError, match="duplicate TransitionHook ID"):
        RutterRegistry(
            {"probe": _definition(_done_states(), transition_hooks=makers)},
            reckoning_root,
        )


@pytest.mark.parametrize(
    "states",
    (
        {
            "start": LLMStep(
                "Choose.",
                answer=AnswerSpec({"yes": {}, "no": {}}),
                next_on_outcome={"yes": "complete"},
            ),
            "complete": Terminal(VoyageResult("complete", {})),
        },
        {
            "start": LLMStep(
                "Choose.",
                answer=AnswerSpec({"yes": {}}),
                next_on_outcome={"yes": "complete", "unknown": "complete"},
            ),
            "complete": Terminal(VoyageResult("complete", {})),
        },
    ),
)
def test_binding_rejects_prompt_routes_with_missing_or_undeclared_outcomes(
    reckoning_root: Path,
    states: Mapping[str, object],
) -> None:
    with pytest.raises(RutterDefinitionError, match="LLMStep routes"):
        RutterRegistry({"probe": _definition(states)}, reckoning_root)


@pytest.mark.parametrize(
    "states",
    (
        {
            "start": LLMStep(
                "Continue.", answer=AnswerSpec({"yes": {}}), next_on_outcome="absent"
            )
        },
        {
            "start": MachineStep(
                lambda context: None,  # type: ignore[arg-type,return-value]
                mode="pure",
                next_on_outcome={"done": "absent"},
            )
        },
        {
            "start": SubRutter(
                DirectChildRutter,
                charter=child_charter,
                next_on_outcome="absent",
            )
        },
    ),
)
def test_binding_rejects_undeclared_literal_successors(
    reckoning_root: Path,
    states: Mapping[str, object],
) -> None:
    with pytest.raises(RutterDefinitionError, match="undeclared successor"):
        RutterRegistry({"probe": _definition(states)}, reckoning_root)


@pytest.mark.parametrize(
    ("states", "callback_name"),
    (
        (
            {
                "start": LLMStep(
                    "Continue.",
                    answer=AnswerSpec({"yes": {}}),
                    data=lambda: {},
                    next_on_outcome="complete",
                ),
                "complete": Terminal(VoyageResult("complete", {})),
            },
            "LLMStep data",
        ),
        (
            {
                "start": LLMStep(
                    "Continue.",
                    answer=AnswerSpec({"yes": {}}),
                    validate=lambda one, two: None,
                    next_on_outcome="complete",
                ),
                "complete": Terminal(VoyageResult("complete", {})),
            },
            "LLMStep validate",
        ),
        (
            {
                "start": MachineStep(
                    lambda: None,  # type: ignore[arg-type,return-value]
                    mode="pure",
                    next_on_outcome="complete",
                ),
                "complete": Terminal(VoyageResult("complete", {})),
            },
            "MachineStep run",
        ),
        (
            {
                "start": SubRutter(
                    DirectChildRutter,
                    charter=lambda: {},
                    next_on_outcome="complete",
                ),
                "complete": Terminal(VoyageResult("complete", {})),
            },
            "SubRutter charter",
        ),
        (
            {"start": Terminal(lambda: VoyageResult("complete", {}))},
            "Terminal result",
        ),
    ),
)
def test_binding_rejects_bad_callback_signatures(
    reckoning_root: Path,
    states: Mapping[str, object],
    callback_name: str,
) -> None:
    with pytest.raises(RutterDefinitionError, match=callback_name):
        RutterRegistry({"probe": _definition(states)}, reckoning_root)


def test_binding_rejects_bad_case_maker_callback_signature(
    reckoning_root: Path,
) -> None:
    maker = transition_hook_probe(
        "attached",
        DirectChildRutter,
        lambda one, two: {},
    )

    with pytest.raises(RutterDefinitionError, match="TransitionHook charter"):
        RutterRegistry(
            {"probe": _definition(_done_states(), transition_hooks=(maker,))},
            reckoning_root,
        )


@pytest.mark.parametrize(
    "run_attribute", ("_store", "reckoning", "path", "revision", "run_data")
)
def test_binding_rejects_run_state_on_definition_instances(
    reckoning_root: Path,
    run_attribute: str,
) -> None:
    class StatefulDefinition(Rutter):
        rutter_id = "stateful"
        definition_version = 1
        initial_evolution_id = "start"

        def __init__(self) -> None:
            setattr(self, run_attribute, object())

        def define_evolutions(self):
            return _done_states()

    with pytest.raises(RutterDefinitionError, match="voyage state"):
        RutterRegistry({"stateful": StatefulDefinition}, reckoning_root)


def test_binding_rejects_child_identity_conflicts(reckoning_root: Path) -> None:
    class First(Rutter):
        rutter_id = "conflict"
        definition_version = 1
        initial_evolution_id = "start"

        def define_evolutions(self):
            return _done_states()

    class Second(First):
        pass

    states = {
        "first": SubRutter(First, charter=child_charter, next_on_outcome="second"),
        "second": SubRutter(Second, charter=child_charter, next_on_outcome="complete"),
        "complete": Terminal(VoyageResult("complete", {})),
    }

    with pytest.raises(RutterDefinitionError, match="identity conflict"):
        RutterRegistry(
            {"root": _definition(states, initial_evolution_id="first")}, reckoning_root
        )


def test_binding_rejects_recursive_definition_call_cycles(
    reckoning_root: Path,
) -> None:
    class First(Rutter):
        rutter_id = "first"
        definition_version = 1
        initial_evolution_id = "call"

        def define_evolutions(self):
            return {
                "call": SubRutter(Second, charter=child_charter, next_on_outcome="done"),
                "done": Terminal(VoyageResult("done", {})),
            }

    class Second(Rutter):
        rutter_id = "second"
        definition_version = 1
        initial_evolution_id = "call"

        def define_evolutions(self):
            return {
                "call": SubRutter(First, charter=child_charter, next_on_outcome="done"),
                "done": Terminal(VoyageResult("done", {})),
            }

    with pytest.raises(RutterDefinitionError, match="definition-call cycle"):
        RutterRegistry({"first": First}, reckoning_root)


def test_binding_discovers_call_hook_and_grandchild_definitions_once(
    reckoning_root: Path,
) -> None:
    GrandchildRutter.constructions = 0

    registry = RutterRegistry({"root": DiscoveryRootRutter}, reckoning_root)
    registry.create("root", Path("first.reckoning.json"), {})
    registry.open(Path("first.reckoning.json"))

    assert GrandchildRutter.constructions == 1


def test_registry_accepts_class_instance_and_no_argument_factory(
    reckoning_root: Path,
) -> None:
    instance = ExampleRutter()
    factory_calls: list[None] = []

    def factory() -> Rutter:
        factory_calls.append(None)
        return DiscoveryRootRutter()

    class_registry = RutterRegistry({"class": DirectChildRutter}, reckoning_root)
    instance_registry = RutterRegistry({"instance": instance}, reckoning_root)
    factory_registry = RutterRegistry({"factory": factory}, reckoning_root)

    assert class_registry.create("class", Path("class.reckoning.json"), {})
    assert instance_registry.create("instance", Path("instance.reckoning.json"), {})
    assert factory_registry.create("factory", Path("factory.reckoning.json"), {})
    assert factory_calls == [None]


def test_registry_freezes_mapping_graph_and_identity_metadata(
    reckoning_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authored = {"start": Terminal(VoyageResult("complete", {}))}
    instance = _definition(authored)()
    registrations: dict[str, object] = {"probe": instance}
    registry = RutterRegistry(registrations, reckoning_root)
    authored.clear()
    registrations.clear()

    registry.create("probe", Path("frozen.reckoning.json"), {})
    monkeypatch.setattr(type(instance), "rutter_id", "changed")
    with pytest.raises(RutterDefinitionError, match="changed after binding"):
        registry.create("probe", Path("drift.reckoning.json"), {})


def test_definition_instances_remain_run_neutral_and_voyage_owns_authority(
    reckoning_root: Path,
) -> None:
    definition = ExampleRutter()
    voyage = RutterRegistry({"example": definition}, reckoning_root).create(
        "example", Path("bound.reckoning.json"), {"artifact": "draft.md"}
    )

    assert not vars(definition)
    assert voyage._reckoning.root.charter == Charter({"artifact": "draft.md"})
    assert isinstance(voyage._store, ReckoningStore)
    assert voyage._store._path == (reckoning_root / "bound.reckoning.json").absolute()


def test_voyage_layer_performs_the_single_open_read(
    reckoning_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = RutterRegistry({"example": ExampleRutter}, reckoning_root)
    path = Path("opened-by-voyage.reckoning.json")
    registry.create("example", path, {})
    reads = 0

    class TrackedStore(ReckoningStore):
        def read(self):
            nonlocal reads
            reads += 1
            return super().read()

    monkeypatch.setattr(engine_module, "ReckoningStore", TrackedStore)

    opened = registry.open(path)

    assert opened.get_status().current_evolution.condition == "ready"
    assert reads == 1


def test_registry_and_voyage_expose_only_the_public_operating_protocol(
    registry: RutterRegistry,
) -> None:
    voyage = registry.create("friendly-example", Path("api.reckoning.json"), {})

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
    assert type(voyage) is Voyage
    assert tuple(inspect.signature(voyage.get_status).parameters) == ()
    assert tuple(inspect.signature(voyage.validate).parameters) == ("response",)
    assert tuple(inspect.signature(voyage.next).parameters) == (
        "response",
        "continue_",
        "dry_run",
    )
    assert tuple(inspect.signature(voyage.help).parameters) == ()
    assert voyage.compass_facing_methods == ("get_status", "validate", "next")
    assert {
        name for name in dir(voyage) if not name.startswith("_")
    } == {
        "compass_facing_methods",
        "get_status",
        "help",
        "next",
        "validate",
    }
    for obsolete in (
        "get_current_node",
        "get_instruction",
        "get_instructions",
        "advance",
        "inspect",
        "start",
        "resume",
    ):
        assert not hasattr(voyage, obsolete)


def test_voyage_help_describes_only_allowlisted_bound_methods_in_order(
    registry: RutterRegistry,
) -> None:
    """A stale handwritten Compass loop would no longer match the Voyage API."""

    voyage = registry.create("friendly-example", Path("help.reckoning.json"), {})

    help_text = voyage.help()

    expected_entries = []
    for name in voyage.compass_facing_methods:
        method = getattr(voyage, name)
        expected_entries.append(
            f"{name}{inspect.signature(method)}\n{inspect.getdoc(method)}"
        )
    expected = "\n\n".join(expected_entries)
    assert help_text == expected
    assert "= MISSING" in help_text
    assert "0x" not in help_text
    assert "_open" not in help_text
    assert "help()" not in help_text


def test_voyage_help_explains_the_message_response_handoff(
    registry: RutterRegistry,
) -> None:
    """Compass must be able to answer its first Message from help alone."""

    voyage = registry.create(
        "friendly-example", Path("message-help.reckoning.json"), {}
    )
    help_text = voyage.help()

    for token in (
        "current_evolution.condition",
        'instructions["text"]',
        'instructions["answer"]',
        'data["payload"]',
        'data["evolution"]["revision"]',
        '"revision"',
        '"outcome"',
        '"evidence"',
    ):
        assert token in help_text


@pytest.mark.parametrize(
    ("methods", "message"),
    (
        (("missing",), "missing"),
        (("_open",), "private"),
        (("compass_facing_methods",), "callable"),
    ),
)
def test_voyage_help_rejects_invalid_compass_facing_methods(
    registry: RutterRegistry,
    monkeypatch: pytest.MonkeyPatch,
    methods: tuple[str, ...],
    message: str,
) -> None:
    """Malformed advertised operations must fail instead of yielding partial help."""

    voyage = registry.create("friendly-example", Path("bad-help.reckoning.json"), {})
    monkeypatch.setattr(Voyage, "compass_facing_methods", methods)

    with pytest.raises(RutterDefinitionError, match=message):
        voyage.help()


def test_voyage_help_rejects_undocumented_advertised_method(
    registry: RutterRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An undocumented operation cannot silently become Compass authority."""

    voyage = registry.create(
        "friendly-example", Path("undocumented-help.reckoning.json"), {}
    )
    monkeypatch.setattr(Voyage, "undocumented", lambda self: None, raising=False)
    monkeypatch.setattr(Voyage, "compass_facing_methods", ("undocumented",))

    with pytest.raises(RutterDefinitionError, match="docstring"):
        voyage.help()


def test_voyage_help_rejects_an_uninspectable_advertised_method(
    registry: RutterRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reflection failure must remain a clear public definition error."""

    def uninspectable(self) -> None:
        """An advertised operation with deliberately invalid signature metadata."""

    uninspectable.__signature__ = object()  # type: ignore[attr-defined]
    voyage = registry.create(
        "friendly-example", Path("uninspectable-help.reckoning.json"), {}
    )
    monkeypatch.setattr(Voyage, "uninspectable", uninspectable, raising=False)
    monkeypatch.setattr(Voyage, "compass_facing_methods", ("uninspectable",))

    with pytest.raises(RutterDefinitionError, match="signature"):
        voyage.help()


def test_voyage_owns_concrete_engine_operations(
    registry: RutterRegistry,
) -> None:
    voyage = registry.create("friendly-example", Path("owned.reckoning.json"), {})

    assert voyage._definition is registry._by_name["friendly-example"]
    assert type(voyage).get_status.__module__ == "officina.rutter.engine"
    assert type(voyage).validate.__module__ == "officina.rutter.engine"
    assert type(voyage).next.__module__ == "officina.rutter.engine"


def test_registry_create_delegates_complete_initial_reckoning_to_engine(
    registry: RutterRegistry,
    reckoning_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    charter = Charter({"artifact": "draft.md"})
    initial = Reckoning(
        3,
        1,
        ActiveRun(
            "engine-run",
            "example",
            1,
            charter,
            EnteredEvolution("entry-report", "report"),
            (
                Turn(
                    "turn-report",
                    "entry-report",
                    "report",
                    1,
                    example_message(),
                    None,
                ),
            ),
            None,
        ),
        {},
        None,
        None,
    )
    calls: list[tuple[object, Charter]] = []

    def create_reckoning(bound_definition: object, supplied: Charter) -> Reckoning:
        calls.append((bound_definition, supplied))
        return initial

    monkeypatch.setattr(runtime_module, "_create_reckoning", create_reckoning)

    voyage = registry.create(
        "friendly-example",
        Path("engine-created.reckoning.json"),
        {"artifact": "draft.md"},
    )

    assert voyage._reckoning is initial
    assert calls == [(registry._by_name["friendly-example"], charter)]
    assert ReckoningStore(
        (reckoning_root / "engine-created.reckoning.json").absolute()
    ).read() == initial


def _unknown_completed_run() -> CompletedRun:
    return CompletedRun(
        "archived-run",
        "retired-child",
        9,
        Charter({}),
        (
            TerminalRecord(
                "archived-done",
                "archived-entry",
                "retired-done",
                VoyageResult("complete", {}),
            ),
        ),
    )


@pytest.mark.parametrize(
    "history",
    (
        (),
        (
            Turn(
                "turn-other",
                "entry-other",
                "report",
                1,
                _prompt_message("entry-other"),
                None,
            ),
        ),
        (
            Turn(
                "turn-first",
                "entry-report",
                "report",
                1,
                _prompt_message("entry-report"),
                Response(1, "reported", {}),
            ),
            Turn(
                "turn-second",
                "entry-report",
                "report",
                1,
                _prompt_message("entry-report"),
                Response(1, "reported", {}),
            ),
        ),
        (
            Turn(
                "turn-altered",
                "entry-report",
                "report",
                1,
                Message(
                    instructions={
                        "text": "Altered.",
                        "answer": {"reported": {}},
                    },
                    data={
                        "evolution": {
                            "id": "report",
                            "entry_id": "entry-report",
                            "revision": 1,
                        },
                        "payload": {},
                    },
                ),
                None,
            ),
        ),
    ),
)
def test_open_rejects_missing_mismatched_duplicate_or_stranded_prompt_turn(
    reckoning_root: Path,
    history: tuple[Turn, ...],
) -> None:
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-report", "report"),
        history,
        None,
    )
    path = (reckoning_root / "invalid-prompt.reckoning.json").absolute()
    ReckoningStore(path).create(Reckoning(3, 1, root, {}, None, None))

    with pytest.raises(RutterStateError, match="active LLMStep"):
        RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
            Path("invalid-prompt.reckoning.json")
        )


def test_open_rejects_unanswered_prompt_turn_with_stale_global_revision(
    reckoning_root: Path,
) -> None:
    turn = Turn(
        "turn-root",
        "entry-report",
        "report",
        1,
        _prompt_message("entry-report"),
        None,
    )
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-report", "report"),
        (turn,),
        None,
    )
    path = (reckoning_root / "stale-prompt-turn.reckoning.json").absolute()
    ReckoningStore(path).create(Reckoning(3, 2, root, {}, None, None))

    with pytest.raises(RutterStateError, match="active LLMStep Turn revision"):
        RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
            Path("stale-prompt-turn.reckoning.json")
        )


def test_open_rejects_open_prompt_turn_with_active_attached_child(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {
            "start": LLMStep(
                "Report.",
                answer=AnswerSpec({"reported": {}}),
                next_on_outcome="complete",
            ),
            "complete": Terminal(VoyageResult("complete", {})),
        },
        transition_hooks=(
            transition_hook_probe("attached", DirectChildRutter, child_charter),
        ),
    )
    turn = Turn(
        "turn-root",
        "entry-root",
        "start",
        1,
        _prompt_message("entry-root", "start"),
        None,
    )
    child = ActiveRun(
        "child-run",
        "direct-child",
        1,
        Charter({}),
        EnteredEvolution("entry-child", "complete"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredEvolution("entry-root", "start"),
        (turn,),
        ActiveChild(
            "attached-call",
            "attached_case",
            "attached",
            turn.record_id,
            child,
        ),
    )
    path = (reckoning_root / "open-with-child.reckoning.json").absolute()
    ReckoningStore(path).create(Reckoning(3, 1, root, {}, None, None))

    with pytest.raises(RutterStateError, match="active LLMStep"):
        RutterRegistry({"probe": definition}, reckoning_root).open(
            Path("open-with-child.reckoning.json")
        )


def test_open_accepts_accepted_prompt_turn_with_matching_attached_child(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {
            "start": LLMStep(
                "Report.",
                answer=AnswerSpec({"reported": {}}),
                next_on_outcome="complete",
            ),
            "complete": Terminal(VoyageResult("complete", {})),
        },
        transition_hooks=(
            transition_hook_probe("attached", DirectChildRutter, child_charter),
        ),
    )
    turn = Turn(
        "turn-root",
        "entry-root",
        "start",
        1,
        _prompt_message("entry-root", "start"),
        Response(1, "reported", {}),
    )
    child = ActiveRun(
        "child-run",
        "direct-child",
        1,
        Charter({}),
        EnteredEvolution("entry-child", "complete"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredEvolution("entry-root", "start"),
        (turn,),
        ActiveChild(
            "attached-call",
            "attached_case",
            "attached",
            turn.record_id,
            child,
        ),
    )
    path = (reckoning_root / "accepted-with-child.reckoning.json").absolute()
    reckoning = Reckoning(3, 1, root, {}, None, None)
    ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"probe": definition}, reckoning_root).open(
        Path("accepted-with-child.reckoning.json")
    )

    assert opened._reckoning == reckoning


def test_open_accepts_prompt_immediately_after_response_acceptance(
    reckoning_root: Path,
) -> None:
    turn = Turn(
        "turn-root",
        "entry-report",
        "report",
        1,
        _prompt_message("entry-report"),
        Response(1, "reported", {}),
    )
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-report", "report"),
        (turn,),
        None,
    )
    path = (reckoning_root / "accepted-prompt.reckoning.json").absolute()
    reckoning = Reckoning(3, 2, root, {}, None, None)
    ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
        Path("accepted-prompt.reckoning.json")
    )

    assert opened._reckoning == reckoning


def test_open_accepts_prompt_after_attached_child_return(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {
            "start": LLMStep(
                "Report.",
                answer=AnswerSpec({"reported": {}}),
                next_on_outcome="complete",
            ),
            "complete": Terminal(VoyageResult("complete", {})),
        },
        transition_hooks=(
            transition_hook_probe("attached", DirectChildRutter, child_charter),
        ),
    )
    turn = Turn(
        "turn-root",
        "entry-root",
        "start",
        1,
        _prompt_message("entry-root", "start"),
        Response(1, "reported", {}),
    )
    completed = _unknown_completed_run()
    returned = SubRutterRecord(
        "attached-call",
        "entry-root",
        None,
        "attached",
        turn.record_id,
        completed.run_id,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredEvolution("entry-root", "start"),
        (turn, returned),
        None,
    )
    path = (reckoning_root / "returned-prompt.reckoning.json").absolute()
    reckoning = Reckoning(
        3,
        3,
        root,
        {completed.run_id: completed},
        None,
        None,
    )
    ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"probe": definition}, reckoning_root).open(
        Path("returned-prompt.reckoning.json")
    )

    assert opened._reckoning == reckoning


def test_open_accepts_historical_prompt_revision_with_open_prompt_child(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {
            "start": LLMStep(
                "Report.",
                answer=AnswerSpec({"reported": {}}),
                next_on_outcome="complete",
            ),
            "complete": Terminal(VoyageResult("complete", {})),
        },
        transition_hooks=(
            transition_hook_probe("attached", ExampleRutter, child_charter),
        ),
    )
    parent_turn = Turn(
        "turn-root",
        "entry-root",
        "start",
        1,
        _prompt_message("entry-root", "start"),
        Response(1, "reported", {}),
    )
    child_turn = Turn(
        "turn-child",
        "entry-child",
        "report",
        2,
        _prompt_message("entry-child", revision=2),
        None,
    )
    child = ActiveRun(
        "child-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-child", "report"),
        (child_turn,),
        None,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredEvolution("entry-root", "start"),
        (parent_turn,),
        ActiveChild(
            "attached-call",
            "attached_case",
            "attached",
            parent_turn.record_id,
            child,
        ),
    )
    path = (reckoning_root / "nested-prompt.reckoning.json").absolute()
    reckoning = Reckoning(3, 2, root, {}, None, None)
    ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"probe": definition}, reckoning_root).open(
        Path("nested-prompt.reckoning.json")
    )

    assert opened._reckoning == reckoning


def test_open_accepts_faulted_prompt_after_response_without_child(
    reckoning_root: Path,
) -> None:
    turn = Turn(
        "turn-root",
        "entry-report",
        "report",
        1,
        _prompt_message("entry-report"),
        Response(1, "reported", {}),
    )
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-report", "report"),
        (turn,),
        None,
    )
    fault = KnownFault("routing", "root-run", "report", "entry-report", None, ())
    path = (reckoning_root / "faulted-prompt.reckoning.json").absolute()
    reckoning = Reckoning(3, 1, root, {}, None, fault)
    ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
        Path("faulted-prompt.reckoning.json")
    )

    assert opened._reckoning == reckoning
    assert type(opened._reckoning.fault).__name__ == "KnownFault"


@pytest.mark.parametrize(
    "fault",
    (
        KnownFault("routing", "other-run", "report", "entry-report", None, ()),
        KnownFault("routing", "root-run", "other-state", "entry-report", None, ()),
        KnownFault("routing", "root-run", "report", "other-entry", None, ()),
    ),
)
def test_open_rejects_accepted_prompt_without_child_for_mismatched_fault(
    reckoning_root: Path,
    fault: KnownFault,
) -> None:
    turn = Turn(
        "turn-root",
        "entry-report",
        "report",
        1,
        _prompt_message("entry-report"),
        Response(1, "reported", {}),
    )
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("entry-report", "report"),
        (turn,),
        None,
    )
    path = (reckoning_root / "mismatched-fault.reckoning.json").absolute()
    ReckoningStore(path).create(Reckoning(3, 1, root, {}, None, fault))

    with pytest.raises(RutterStateError, match="accepted active LLMStep"):
        RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
            Path("mismatched-fault.reckoning.json")
        )


def test_open_accepts_current_done_with_matching_terminal_attached_child(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {"start": Terminal(VoyageResult("complete", {}))},
        transition_hooks=(
            transition_hook_probe("attached", DirectChildRutter, child_charter),
        ),
    )
    done = TerminalRecord(
        "done-root",
        "entry-root",
        "start",
        VoyageResult("complete", {}),
    )
    child = ActiveRun(
        "child-run",
        "direct-child",
        1,
        Charter({}),
        EnteredEvolution("entry-child", "complete"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredEvolution("entry-root", "start"),
        (done,),
        ActiveChild(
            "attached-call",
            "attached_case",
            "attached",
            done.record_id,
            child,
        ),
    )
    path = (reckoning_root / "terminal-child.reckoning.json").absolute()
    reckoning = Reckoning(3, 1, root, {}, None, None)
    ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"probe": definition}, reckoning_root).open(
        Path("terminal-child.reckoning.json")
    )

    assert opened._reckoning == reckoning


def test_open_does_not_require_definitions_for_archived_completed_runs(
    reckoning_root: Path,
) -> None:
    completed = _unknown_completed_run()
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredEvolution("root-entry", "report"),
        (
            SubRutterRecord(
                "archived-call",
                "retired-entry",
                "retired-site",
                None,
                None,
                completed.run_id,
            ),
            Turn(
                "current-turn",
                "root-entry",
                "report",
                1,
                _prompt_message("root-entry"),
                None,
            ),
        ),
        None,
    )
    reckoning = Reckoning(3, 1, root, {completed.run_id: completed}, None, None)
    path = (reckoning_root / "archived.reckoning.json").absolute()
    ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
        Path("archived.reckoning.json")
    )

    assert opened._reckoning == reckoning


def test_open_does_not_resolve_inactive_reachable_child_metadata(
    reckoning_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry = RutterRegistry({"root": DiscoveryRootRutter}, reckoning_root)
    registry.create("root", Path("inactive-child.reckoning.json"), {})
    monkeypatch.setattr(DirectChildRutter, "definition_version", 2)

    opened = registry.open(Path("inactive-child.reckoning.json"))

    assert opened._reckoning.root.rutter_id == "discovery-root"


def test_open_requires_every_definition_on_the_recursively_active_path(
    reckoning_root: Path,
) -> None:
    unknown = ActiveRun(
        "unknown-run",
        "missing-active-child",
        1,
        Charter({}),
        EnteredEvolution("unknown-entry", "missing-state"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "discovery-root",
        1,
        Charter({}),
        EnteredEvolution("root-entry", "delegate"),
        (),
        ActiveChild("active-call", "explicit_call", "delegate", None, unknown),
    )
    path = (reckoning_root / "active.reckoning.json").absolute()
    ReckoningStore(path).create(Reckoning(3, 0, root, {}, None, None))

    with pytest.raises(RutterStateError, match="active Rutter definition"):
        RutterRegistry({"root": DiscoveryRootRutter}, reckoning_root).open(
            Path("active.reckoning.json")
        )


@pytest.mark.parametrize(
    "reckoning_path",
    (
        Path("/absolute.reckoning.json"),
        Path("../escape.reckoning.json"),
        Path("nested/../escape.reckoning.json"),
        Path("job.reckoning.json.lock"),
    ),
)
def test_registry_preserves_path_confinement(
    registry: RutterRegistry,
    reckoning_path: Path,
) -> None:
    with pytest.raises(RutterDefinitionError):
        registry.create("friendly-example", reckoning_path, {})
    with pytest.raises(RutterDefinitionError):
        registry.open(reckoning_path)


def test_registry_open_waits_for_the_reckoning_lock(
    registry: RutterRegistry,
    reckoning_root: Path,
) -> None:
    registry.create("friendly-example", Path("locked.reckoning.json"), {})
    writer = ReckoningStore((reckoning_root / "locked.reckoning.json").absolute())
    attempted = Event()
    opened: list[object] = []

    def open_from_registry() -> None:
        attempted.set()
        opened.append(registry.open(Path("locked.reckoning.json")))

    with writer.transaction():
        thread = Thread(target=open_from_registry)
        thread.start()
        assert attempted.wait(timeout=1)
        thread.join(timeout=0.1)
        assert not opened
    thread.join(timeout=1)

    assert len(opened) == 1


def test_runtime_does_not_export_a_compatibility_facade() -> None:
    assert runtime_module.__all__ == ("RutterRegistry",)
    for removed in (
        "BaseRutter",
        "RutterFactory",
        "give_instructions",
        "validate_result",
        "update",
        "advance",
    ):
        assert not hasattr(runtime_module, removed)
