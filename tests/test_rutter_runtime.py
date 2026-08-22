"""Specify pure definition binding and the registry-owned voyage boundary."""

from __future__ import annotations

import inspect
from collections.abc import Iterator
from pathlib import Path
import sys
from threading import Event, Thread
from types import SimpleNamespace
from typing import Mapping

import pytest

import officina.rutter.runtime as runtime_module
from officina.rutter.model import (
    Action,
    ActiveChild,
    ActiveRun,
    AnswerSpec,
    Call,
    CallRecord,
    Charter,
    CompletedRun,
    Done,
    DoneRecord,
    EnteredNode,
    Message,
    Prompt,
    Reckoning,
    Response,
    RunResult,
    Rutter,
    RutterDefinitionError,
    RutterStateError,
    Turn,
)
from officina.rutter.runtime import RutterRegistry
from officina.rutter.storage import _ReckoningStore
from test_support.rutter_fixtures import (
    AttachedChildRutter,
    CaseMakerProbe,
    DirectChildRutter,
    DiscoveryRootRutter,
    ExampleRutter,
    GrandchildRutter,
    child_charter,
    example_message,
)


def _definition(
    states: Mapping[str, object],
    *,
    rutter_id: object = "probe",
    definition_version: object = 1,
    start_state: object = "start",
    allow_multiple_cases_at_once: object = False,
    case_makers: tuple[object, ...] = (),
) -> type[Rutter]:
    class Definition(Rutter):
        def define_states(self):
            return states

        def define_case_makers(self):
            return case_makers

    Definition.rutter_id = rutter_id  # type: ignore[assignment]
    Definition.definition_version = definition_version  # type: ignore[assignment]
    Definition.start_state = start_state  # type: ignore[assignment]
    Definition.allow_multiple_cases_at_once = allow_multiple_cases_at_once  # type: ignore[assignment]
    return Definition


def _done_states() -> Mapping[str, object]:
    return {"start": Done(RunResult("complete", {}))}


def _prompt_message(
    entry_id: str,
    state_id: str = "report",
    revision: int = 1,
) -> Message:
    return Message(
        instructions={"text": "Report.", "answer": {"reported": {}}},
        data={
            "state": {
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


@pytest.fixture(autouse=True)
def _task_five_constructor_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    def create_reckoning(bound_definition: object, charter: Charter) -> Reckoning:
        entry_id = "test-entry"
        state_id = bound_definition.start_state
        state = bound_definition.states[state_id]
        history = ()
        if isinstance(state, Prompt):
            message = Message(
                instructions={
                    "text": state.text,
                    "answer": state.answer.to_json(),
                },
                data={
                    "state": {
                        "id": state_id,
                        "entry_id": entry_id,
                        "revision": 0,
                    },
                    "payload": {},
                },
            )
            history = (
                Turn("test-turn", entry_id, state_id, 0, message, None),
            )
        return Reckoning(
            3,
            0,
            ActiveRun(
                "test-run",
                bound_definition.rutter_id,
                bound_definition.definition_version,
                charter,
                EnteredNode(entry_id, state_id),
                history,
                None,
            ),
            {},
            None,
            None,
        )

    monkeypatch.setitem(
        sys.modules,
        "officina.rutter.engine",
        SimpleNamespace(_create_reckoning=create_reckoning),
    )


@pytest.mark.parametrize(
    ("attribute", "value", "message"),
    (
        ("rutter_id", "", "Rutter ID"),
        ("rutter_id", "bad id", "Rutter ID"),
        ("definition_version", 0, "definition_version"),
        ("definition_version", True, "definition_version"),
        ("start_state", "missing", "start_state"),
        ("allow_multiple_cases_at_once", 1, "allow_multiple_cases_at_once"),
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
        "start_state": "start",
        "allow_multiple_cases_at_once": False,
    }
    values[attribute] = value
    definition = _definition(_done_states(), **values)

    with pytest.raises(RutterDefinitionError, match=message):
        RutterRegistry({"probe": definition}, reckoning_root)


class _DuplicateStateMapping(Mapping[str, object]):
    def __getitem__(self, key: str) -> object:
        if key != "start":
            raise KeyError(key)
        return Done(RunResult("complete", {}))

    def __iter__(self) -> Iterator[str]:
        return iter(("start", "start"))

    def __len__(self) -> int:
        return 2


def test_binding_rejects_duplicate_state_ids(reckoning_root: Path) -> None:
    with pytest.raises(RutterDefinitionError, match="duplicate state ID"):
        RutterRegistry(
            {"probe": _definition(_DuplicateStateMapping())},
            reckoning_root,
        )


def test_binding_rejects_duplicate_case_maker_ids(reckoning_root: Path) -> None:
    makers = (
        CaseMakerProbe("same", DirectChildRutter, child_charter),
        CaseMakerProbe("same", AttachedChildRutter, child_charter),
    )

    with pytest.raises(RutterDefinitionError, match="duplicate CaseMaker ID"):
        RutterRegistry(
            {"probe": _definition(_done_states(), case_makers=makers)},
            reckoning_root,
        )


@pytest.mark.parametrize(
    "states",
    (
        {
            "start": Prompt(
                "Choose.",
                answer=AnswerSpec({"yes": {}, "no": {}}),
                then={"yes": "complete"},
            ),
            "complete": Done(RunResult("complete", {})),
        },
        {
            "start": Prompt(
                "Choose.",
                answer=AnswerSpec({"yes": {}}),
                then={"yes": "complete", "unknown": "complete"},
            ),
            "complete": Done(RunResult("complete", {})),
        },
    ),
)
def test_binding_rejects_prompt_routes_with_missing_or_undeclared_outcomes(
    reckoning_root: Path,
    states: Mapping[str, object],
) -> None:
    with pytest.raises(RutterDefinitionError, match="Prompt routes"):
        RutterRegistry({"probe": _definition(states)}, reckoning_root)


@pytest.mark.parametrize(
    "states",
    (
        {
            "start": Prompt(
                "Continue.", answer=AnswerSpec({"yes": {}}), then="absent"
            )
        },
        {
            "start": Action(
                lambda context: None,  # type: ignore[arg-type,return-value]
                mode="pure",
                then={"done": "absent"},
            )
        },
        {
            "start": Call(
                DirectChildRutter,
                charter=child_charter,
                then="absent",
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
                "start": Prompt(
                    "Continue.",
                    answer=AnswerSpec({"yes": {}}),
                    data=lambda: {},
                    then="complete",
                ),
                "complete": Done(RunResult("complete", {})),
            },
            "Prompt data",
        ),
        (
            {
                "start": Prompt(
                    "Continue.",
                    answer=AnswerSpec({"yes": {}}),
                    validate=lambda one, two: None,
                    then="complete",
                ),
                "complete": Done(RunResult("complete", {})),
            },
            "Prompt validate",
        ),
        (
            {
                "start": Action(
                    lambda: None,  # type: ignore[arg-type,return-value]
                    mode="pure",
                    then="complete",
                ),
                "complete": Done(RunResult("complete", {})),
            },
            "Action run",
        ),
        (
            {
                "start": Call(
                    DirectChildRutter,
                    charter=lambda: {},
                    then="complete",
                ),
                "complete": Done(RunResult("complete", {})),
            },
            "Call charter",
        ),
        (
            {"start": Done(lambda: RunResult("complete", {}))},
            "Done result",
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
    maker = CaseMakerProbe(
        "attached",
        DirectChildRutter,
        lambda one, two: {},
    )

    with pytest.raises(RutterDefinitionError, match="CaseMaker charter"):
        RutterRegistry(
            {"probe": _definition(_done_states(), case_makers=(maker,))},
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
        start_state = "start"

        def __init__(self) -> None:
            setattr(self, run_attribute, object())

        def define_states(self):
            return _done_states()

    with pytest.raises(RutterDefinitionError, match="run state"):
        RutterRegistry({"stateful": StatefulDefinition}, reckoning_root)


def test_binding_rejects_child_identity_conflicts(reckoning_root: Path) -> None:
    class First(Rutter):
        rutter_id = "conflict"
        definition_version = 1
        start_state = "start"

        def define_states(self):
            return _done_states()

    class Second(First):
        pass

    states = {
        "first": Call(First, charter=child_charter, then="second"),
        "second": Call(Second, charter=child_charter, then="complete"),
        "complete": Done(RunResult("complete", {})),
    }

    with pytest.raises(RutterDefinitionError, match="identity conflict"):
        RutterRegistry(
            {"root": _definition(states, start_state="first")}, reckoning_root
        )


def test_binding_rejects_recursive_definition_call_cycles(
    reckoning_root: Path,
) -> None:
    class First(Rutter):
        rutter_id = "first"
        definition_version = 1
        start_state = "call"

        def define_states(self):
            return {
                "call": Call(Second, charter=child_charter, then="done"),
                "done": Done(RunResult("done", {})),
            }

    class Second(Rutter):
        rutter_id = "second"
        definition_version = 1
        start_state = "call"

        def define_states(self):
            return {
                "call": Call(First, charter=child_charter, then="done"),
                "done": Done(RunResult("done", {})),
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
    authored = {"start": Done(RunResult("complete", {}))}
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
    assert isinstance(voyage._store, _ReckoningStore)
    assert voyage._store._path == (reckoning_root / "bound.reckoning.json").absolute()


def test_registry_and_bound_voyage_expose_only_the_frozen_construction_protocol(
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
    assert tuple(inspect.signature(voyage.get_instruction).parameters) == ()
    assert tuple(inspect.signature(voyage.validate).parameters) == ("response",)
    assert tuple(inspect.signature(voyage.next).parameters) == (
        "response",
        "continue_",
        "dry_run",
    )
    assert tuple(inspect.signature(voyage.get_current_node).parameters) == ()
    assert {
        name for name in dir(voyage) if not name.startswith("_")
    } == {"get_instruction", "validate", "next", "get_current_node"}
    for obsolete in ("get_instructions", "advance", "inspect", "start", "resume"):
        assert not hasattr(voyage, obsolete)


def test_bound_voyage_lazily_forwards_exact_operation_arguments_to_engine(
    registry: RutterRegistry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    voyage = registry.create("friendly-example", Path("delegated.reckoning.json"), {})
    response = object()
    instruction = object()
    validation = object()
    next_node = object()
    current_node = object()
    calls: list[tuple[object, ...]] = []

    def get_instruction(bound: object) -> object:
        calls.append(("get_instruction", bound))
        return instruction

    def validate(bound: object, supplied: object) -> object:
        calls.append(("validate", bound, supplied))
        return validation

    def next_(
        bound: object,
        supplied: object,
        *,
        continue_: bool,
        dry_run: bool,
    ) -> object:
        calls.append(("next", bound, supplied, continue_, dry_run))
        return next_node

    def get_current_node(bound: object) -> object:
        calls.append(("get_current_node", bound))
        return current_node

    monkeypatch.setitem(
        sys.modules,
        "officina.rutter.engine",
        SimpleNamespace(
            _get_instruction=get_instruction,
            _validate=validate,
            _next=next_,
            _get_current_node=get_current_node,
        ),
    )

    assert voyage.get_instruction() is instruction
    assert voyage.validate(response) is validation
    assert voyage.next(response, continue_=False, dry_run=True) is next_node
    assert voyage.next() is next_node
    assert voyage.get_current_node() is current_node
    assert calls == [
        ("get_instruction", voyage),
        ("validate", voyage, response),
        ("next", voyage, response, False, True),
        ("next", voyage, runtime_module._MISSING, True, False),
        ("get_current_node", voyage),
    ]


def test_registry_create_lazily_delegates_complete_initial_reckoning_to_engine(
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
            EnteredNode("entry-report", "report"),
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

    monkeypatch.setitem(
        sys.modules,
        "officina.rutter.engine",
        SimpleNamespace(_create_reckoning=create_reckoning),
    )

    voyage = registry.create(
        "friendly-example",
        Path("engine-created.reckoning.json"),
        {"artifact": "draft.md"},
    )

    assert voyage._reckoning is initial
    assert calls == [(registry._by_name["friendly-example"], charter)]
    assert _ReckoningStore(
        (reckoning_root / "engine-created.reckoning.json").absolute()
    ).read() == initial


def _unknown_completed_run() -> CompletedRun:
    return CompletedRun(
        "archived-run",
        "retired-child",
        9,
        Charter({}),
        (
            DoneRecord(
                "archived-done",
                "archived-entry",
                "retired-done",
                RunResult("complete", {}),
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
                "turn-accepted",
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
                        "state": {
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
        EnteredNode("entry-report", "report"),
        history,
        None,
    )
    path = (reckoning_root / "invalid-prompt.reckoning.json").absolute()
    _ReckoningStore(path).create(Reckoning(3, 1, root, {}, None, None))

    with pytest.raises(RutterStateError, match="active Prompt"):
        RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
            Path("invalid-prompt.reckoning.json")
        )


@pytest.mark.parametrize(
    ("response", "fault"),
    (
        (None, None),
        (
            Response(1, "reported", {}),
            {
                "category": "routing",
                "run_id": "root-run",
                "state_id": "report",
                "node_entry_id": "entry-report",
            },
        ),
    ),
    ids=("open", "accepted"),
)
def test_open_rejects_current_prompt_turn_with_stale_global_revision(
    reckoning_root: Path,
    response: Response | None,
    fault: dict[str, object] | None,
) -> None:
    turn = Turn(
        "turn-root",
        "entry-report",
        "report",
        1,
        _prompt_message("entry-report"),
        response,
    )
    root = ActiveRun(
        "root-run",
        "example",
        1,
        Charter({}),
        EnteredNode("entry-report", "report"),
        (turn,),
        None,
    )
    path = (reckoning_root / "stale-prompt-turn.reckoning.json").absolute()
    _ReckoningStore(path).create(Reckoning(3, 2, root, {}, None, fault))

    with pytest.raises(RutterStateError, match="active Prompt Turn revision"):
        RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
            Path("stale-prompt-turn.reckoning.json")
        )


def test_open_rejects_open_prompt_turn_with_active_attached_child(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {
            "start": Prompt(
                "Report.",
                answer=AnswerSpec({"reported": {}}),
                then="complete",
            ),
            "complete": Done(RunResult("complete", {})),
        },
        case_makers=(
            CaseMakerProbe("attached", DirectChildRutter, child_charter),
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
        EnteredNode("entry-child", "complete"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredNode("entry-root", "start"),
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
    _ReckoningStore(path).create(Reckoning(3, 1, root, {}, None, None))

    with pytest.raises(RutterStateError, match="active Prompt"):
        RutterRegistry({"probe": definition}, reckoning_root).open(
            Path("open-with-child.reckoning.json")
        )


def test_open_accepts_accepted_prompt_turn_with_matching_attached_child(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {
            "start": Prompt(
                "Report.",
                answer=AnswerSpec({"reported": {}}),
                then="complete",
            ),
            "complete": Done(RunResult("complete", {})),
        },
        case_makers=(
            CaseMakerProbe("attached", DirectChildRutter, child_charter),
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
        EnteredNode("entry-child", "complete"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredNode("entry-root", "start"),
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
    _ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"probe": definition}, reckoning_root).open(
        Path("accepted-with-child.reckoning.json")
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
        EnteredNode("entry-report", "report"),
        (turn,),
        None,
    )
    fault = {
        "category": "routing",
        "run_id": "root-run",
        "state_id": "report",
        "node_entry_id": "entry-report",
    }
    path = (reckoning_root / "faulted-prompt.reckoning.json").absolute()
    reckoning = Reckoning(3, 1, root, {}, None, fault)
    _ReckoningStore(path).create(reckoning)

    opened = RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
        Path("faulted-prompt.reckoning.json")
    )

    assert opened._reckoning == reckoning


@pytest.mark.parametrize(
    "fault",
    (
        {
            "category": "routing",
            "run_id": "other-run",
            "state_id": "report",
            "node_entry_id": "entry-report",
        },
        {
            "category": "routing",
            "run_id": "root-run",
            "state_id": "other-state",
            "node_entry_id": "entry-report",
        },
        {
            "category": "routing",
            "run_id": "root-run",
            "state_id": "report",
            "node_entry_id": "other-entry",
        },
    ),
)
def test_open_rejects_accepted_prompt_without_child_for_mismatched_fault(
    reckoning_root: Path,
    fault: dict[str, object],
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
        EnteredNode("entry-report", "report"),
        (turn,),
        None,
    )
    path = (reckoning_root / "mismatched-fault.reckoning.json").absolute()
    _ReckoningStore(path).create(Reckoning(3, 1, root, {}, None, fault))

    with pytest.raises(RutterStateError, match="accepted active Prompt"):
        RutterRegistry({"example": ExampleRutter}, reckoning_root).open(
            Path("mismatched-fault.reckoning.json")
        )


def test_open_accepts_current_done_with_matching_terminal_attached_child(
    reckoning_root: Path,
) -> None:
    definition = _definition(
        {"start": Done(RunResult("complete", {}))},
        case_makers=(
            CaseMakerProbe("attached", DirectChildRutter, child_charter),
        ),
    )
    done = DoneRecord(
        "done-root",
        "entry-root",
        "start",
        RunResult("complete", {}),
    )
    child = ActiveRun(
        "child-run",
        "direct-child",
        1,
        Charter({}),
        EnteredNode("entry-child", "complete"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "probe",
        1,
        Charter({}),
        EnteredNode("entry-root", "start"),
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
    _ReckoningStore(path).create(reckoning)

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
        EnteredNode("root-entry", "report"),
        (
            CallRecord(
                "archived-call",
                "retired-entry",
                "explicit_call",
                "retired-site",
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
    _ReckoningStore(path).create(reckoning)

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
        EnteredNode("unknown-entry", "missing-state"),
        (),
        None,
    )
    root = ActiveRun(
        "root-run",
        "discovery-root",
        1,
        Charter({}),
        EnteredNode("root-entry", "delegate"),
        (),
        ActiveChild("active-call", "explicit_call", "delegate", None, unknown),
    )
    path = (reckoning_root / "active.reckoning.json").absolute()
    _ReckoningStore(path).create(Reckoning(3, 0, root, {}, None, None))

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
    writer = _ReckoningStore((reckoning_root / "locked.reckoning.json").absolute())
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
