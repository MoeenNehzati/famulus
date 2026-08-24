"""Specify record-derived transition hooks without reducer phases."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import officina.rutter as rutter_public
from officina.rutter.history import CompletedRun, SubRutterRecord, Transition
from test_support.rutter_fixtures import response_schema as _response_schema


class HookChild(rutter_public.Rutter):
    rutter_id = "hook-child"
    definition_version = 1
    initial_evolution_id = "done"

    def define_evolutions(self) -> dict[str, object]:
        return {"done": rutter_public.Terminal(result=rutter_public.VoyageResult("checked", {}))}


def test_edge_match_constructors_cover_wildcard_and_exact_edges() -> None:
    """Changing a matcher field or treating a wildcard as exact must fail."""

    assert hasattr(rutter_public, "TransitionMatch")
    assert hasattr(rutter_public, "after")
    assert hasattr(rutter_public, "before")
    assert hasattr(rutter_public, "on_transition")
    after = rutter_public.after
    before = rutter_public.before
    on_transition = rutter_public.on_transition
    edge = Transition("record-review", "entry-review", "review", "approved", "publish")

    assert after("review").matches(edge)
    assert before("publish").matches(edge)
    assert on_transition(outcome="approved").matches(edge)
    assert on_transition(
        source="review", outcome="approved", target="publish"
    ).matches(edge)
    assert not after("draft").matches(edge)
    assert not before("archive").matches(edge)
    assert not on_transition(outcome="rejected").matches(edge)
    with pytest.raises(FrozenInstanceError):
        after("review").source = "draft"


def test_case_maker_constructor_freezes_one_validated_hook_definition() -> None:
    """Accepting an unstable ID, matcher, child, or Charter callback must fail."""

    assert hasattr(rutter_public, "TransitionHook")
    maker = rutter_public.TransitionHook(
        "review-check",
        on=rutter_public.after("review"),
        child=HookChild,
        charter_constructor=lambda context: {"source": context.evolution.evolution_id},
    )

    assert maker.id == "review-check"
    assert maker.on == rutter_public.TransitionMatch(source="review")
    assert maker.child is HookChild
    assert callable(maker.charter_constructor)
    assert not hasattr(maker, "charter")
    with pytest.raises(FrozenInstanceError):
        maker.id = "other"
    with pytest.raises(rutter_public.RutterDefinitionError):
        rutter_public.TransitionHook(
            "bad id",
            on=rutter_public.after("review"),
            child=HookChild,
            charter_constructor=lambda context: {},
        )


def _completed(run_id: str) -> CompletedRun:
    result = rutter_public.VoyageResult("checked", {"run": run_id})
    return CompletedRun(
        run_id,
        "hook-child",
        1,
        rutter_public.Charter({}),
        (
            rutter_public.TerminalRecord(
                f"done-{run_id}",
                f"entry-{run_id}",
                "done",
                result,
            ),
        ),
    )


def test_attached_calls_filters_exact_provenance_not_colliding_explicit_sites() -> None:
    """Filtering calls by site alone must not treat an explicit SubRutter as a hook."""

    source = rutter_public.MachineRecord(
        "edge-review",
        "action-review",
        "entry-review",
        "review",
        "pure",
        rutter_public.MachineResult("approved", {}),
    )
    explicit = SubRutterRecord(
        "call-explicit",
        "entry-review",
        "review-check",
        None,
        None,
        "run-explicit",
    )
    attached = SubRutterRecord(
        "call-attached",
        "entry-review",
        None,
        "review-check",
        source.record_id,
        "run-attached",
    )
    history = rutter_public.HistoryView(
        (source, explicit, attached),
        {
            "run-explicit": _completed("run-explicit"),
            "run-attached": _completed("run-attached"),
        },
    )

    assert history.subrutters() == (
        history.latest_subrutter(origin_evolution_id="review-check"),
        history.latest_subrutter(transition_hook_id="review-check"),
    )
    assert history.subrutters(origin_evolution_id="review-check") == (
        history.subrutters()[0],
    )
    assert history.subrutters(transition_hook_id="review-check") == (
        history.subrutters()[1],
    )
    assert history.hook_runs(
        transition_hook_id="review-check",
        transition_id="edge-review",
    ) == (
        history.subrutters()[1],
    )
    assert history.subrutters(transition_hook_id="other") == ()
    assert history.hook_runs(
        transition_hook_id="review-check",
        transition_id="edge-other",
    ) == ()


def test_every_matcher_reuses_its_record_anchored_context_and_provenance(
    tmp_path: Path,
) -> None:
    """Rebuilding a hook edge from later attached calls must not shift its context."""

    seen: dict[str, list[rutter_public.TransitionContext]] = {}

    def remember(maker_id: str):
        def build(context: rutter_public.TransitionContext) -> dict[str, object]:
            seen.setdefault(maker_id, []).append(context)
            return {"maker": maker_id}

        return build

    class HookedParent(rutter_public.Rutter):
        rutter_id = "hooked-parent"
        definition_version = 1
        initial_evolution_id = "review"
        allow_multiple_hooks_per_transition = True

        def define_evolutions(self) -> dict[str, object]:
            return {
                "review": rutter_public.MachineStep(
                    lambda context: rutter_public.MachineResult("approved", {}),
                    mode="pure",
                    next_on_outcome="invoke",
                ),
                "invoke": rutter_public.SubRutter(
                    HookChild,
                    charter_constructor=lambda context: {"kind": "explicit"},
                    next_on_outcome="done",
                ),
                "done": rutter_public.Terminal(result=rutter_public.VoyageResult("finished", {"ok": True})
                ),
            }

        def define_transition_hooks(self) -> tuple[object, ...]:
            return (
                rutter_public.TransitionHook(
                    "after-review",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter_constructor=remember("after-review"),
                ),
                rutter_public.TransitionHook(
                    "before-invoke",
                    on=rutter_public.before("invoke"),
                    child=HookChild,
                    charter_constructor=remember("before-invoke"),
                ),
                rutter_public.TransitionHook(
                    "exact-review",
                    on=rutter_public.on_transition(
                        source="review", outcome="approved", target="invoke"
                    ),
                    child=HookChild,
                    charter_constructor=remember("exact-review"),
                ),
                rutter_public.TransitionHook(
                    "post-call",
                    on=rutter_public.after("invoke"),
                    child=HookChild,
                    charter_constructor=remember("post-call"),
                ),
                rutter_public.TransitionHook(
                    "post-done",
                    on=rutter_public.after("done"),
                    child=HookChild,
                    charter_constructor=remember("post-done"),
                ),
            )

    voyage = rutter_public.RutterRegistry(
        {"parent": HookedParent}, tmp_path
    ).create("parent", Path("hooked.reckoning.json"), {})

    terminal = voyage.advance(continue_=True)

    assert terminal.evolution_id == "done"
    assert terminal.condition == "terminal"
    reckoning = voyage._store.read()
    history = rutter_public.HistoryView(
        reckoning.root.history, reckoning.completed_runs
    )
    attached = tuple(
        call for call in history.subrutters()
        if call.transition_hook_id is not None
    )
    assert tuple(call.transition_hook_id for call in attached) == (
        "after-review",
        "before-invoke",
        "exact-review",
        "post-call",
        "post-done",
    )
    source_by_edge = {
        (entry.invocation_id if isinstance(entry, SubRutterRecord) else entry.record_id): entry
        for entry in reckoning.root.history
        if not (
            isinstance(entry, SubRutterRecord)
            and entry.transition_hook_id is not None
        )
    }
    for call in attached:
        assert call.transition_hook_id is not None
        assert call.attached_to_transition_id in source_by_edge
        source = source_by_edge[call.attached_to_transition_id]
        assert all(
            context.record == source
            for context in seen[call.transition_hook_id]
        )
        assert all(
            context.evolution.history.entries()
            == history.strict_prefix(source).entries()
            for context in seen[call.transition_hook_id]
        )
        assert all(
            context.transition.transition_id == call.attached_to_transition_id
            for context in seen[call.transition_hook_id]
        )


def test_multiple_selection_faults_with_every_maker_before_child_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting one child or omitting a selected ID before policy checks must fail."""

    selected_in_order: list[str] = []

    def choose(maker_id: str, value: dict[str, object] | None):
        def build(context: rutter_public.TransitionContext):
            del context
            selected_in_order.append(maker_id)
            return value

        return build

    class SingleCaseParent(rutter_public.Rutter):
        rutter_id = "single-case-parent"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> dict[str, object]:
            return {
                "review": rutter_public.MachineStep(
                    lambda context: rutter_public.MachineResult("approved", {}),
                    mode="pure",
                    next_on_outcome="done",
                ),
                "done": rutter_public.Terminal(result=rutter_public.VoyageResult("finished", {})),
            }

        def define_transition_hooks(self) -> tuple[object, ...]:
            return (
                rutter_public.TransitionHook(
                    "declined",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter_constructor=choose("declined", None),
                ),
                rutter_public.TransitionHook(
                    "first",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter_constructor=choose("first", {"case": "first"}),
                ),
                rutter_public.TransitionHook(
                    "second",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter_constructor=choose("second", {"case": "second"}),
                ),
            )

    voyage = rutter_public.RutterRegistry(
        {"parent": SingleCaseParent}, tmp_path
    ).create("parent", Path("single-case.reckoning.json"), {})
    allocated: list[str] = []
    original_new_id = __import__(
        "officina.rutter.engine", fromlist=["_new_id"]
    )._new_id

    def observe_allocation(prefix: str) -> str:
        allocated.append(prefix)
        return original_new_id(prefix)

    monkeypatch.setattr("officina.rutter.engine._new_id", observe_allocation)

    fault = voyage.advance(continue_=True)

    assert fault.condition == "fault"
    persisted = voyage._store.read()
    assert persisted.fault is not None
    assert persisted.fault.category == "case-cardinality"
    assert persisted.fault.run_id == persisted.root.run_id
    assert persisted.fault.evolution_id == "review"
    assert (
        persisted.fault.evolution_entry_id
        == persisted.root.entered_evolution.entry_id
    )
    assert persisted.fault.transition_hook_ids == ("first", "second")
    assert selected_in_order == ["declined", "first", "second"]
    assert allocated == ["record"]
    assert persisted.root.active_child is None
    assert persisted.completed_runs == {}
    assert len(persisted.root.history) == 1


@pytest.mark.parametrize(
    ("failure", "category"),
    (("matcher", "case-matcher"), ("charter", "case-charter")),
)
def test_case_callback_failure_preserves_accepted_source_as_stable_fault(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    category: str,
) -> None:
    """Leaking callback errors or rolling back accepted work must fail."""

    def charter(context: rutter_public.TransitionContext) -> dict[str, object]:
        del context
        if failure == "charter":
            raise RuntimeError("private Charter detail")
        return {}

    class FailingCaseParent(rutter_public.Rutter):
        rutter_id = f"failing-case-{failure}"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> dict[str, object]:
            return {
                "review": rutter_public.MachineStep(
                    lambda context: rutter_public.MachineResult("approved", {}),
                    mode="pure",
                    next_on_outcome="done",
                ),
                "done": rutter_public.Terminal(result=rutter_public.VoyageResult("finished", {})),
            }

        def define_transition_hooks(self) -> tuple[object, ...]:
            return (
                rutter_public.TransitionHook(
                    "failing-case",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter_constructor=charter,
                ),
            )

    voyage = rutter_public.RutterRegistry(
        {"parent": FailingCaseParent}, tmp_path
    ).create("parent", Path(f"{failure}.reckoning.json"), {})
    if failure == "matcher":
        def fail_match(self: object, edge: object) -> bool:
            del self, edge
            raise RuntimeError("private matcher detail")

        monkeypatch.setattr(rutter_public.TransitionMatch, "matches", fail_match)

    fault = voyage.advance(continue_=True)

    assert fault.condition == "fault"
    persisted = voyage._store.read()
    assert persisted.fault is not None
    assert persisted.fault.category == category
    assert persisted.fault.run_id == persisted.root.run_id
    assert persisted.fault.evolution_id == "review"
    assert (
        persisted.fault.evolution_entry_id
        == persisted.root.entered_evolution.entry_id
    )
    assert persisted.fault.transition_hook_ids == ("failing-case",)
    assert len(persisted.root.history) == 1
    assert isinstance(persisted.root.history[0], rutter_public.MachineRecord)
    assert persisted.root.active_child is None
    assert persisted.completed_runs == {}


def test_prompt_attachment_reopens_after_attach_settle_and_return_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A returned hook must remain resumable without replaying the accepted LLMStep."""

    seen: list[Transition] = []

    def child_charter(
        context: rutter_public.TransitionContext,
    ) -> dict[str, object]:
        seen.append(context.transition)
        return {
            "source": context.transition.source,
            "outcome": context.transition.outcome,
            "target": context.transition.target,
            "transition_id": context.transition.transition_id,
        }

    class PromptHookParent(rutter_public.Rutter):
        rutter_id = "prompt-hook-parent"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> dict[str, object]:
            return {
                "review": rutter_public.LLMStep(
                    "Review.",
                    response_schema=_response_schema("approved"),
                    next_on_outcome="publish",
                ),
                "publish": rutter_public.Terminal(result=rutter_public.VoyageResult("finished", {})
                ),
            }

        def define_transition_hooks(self) -> tuple[object, ...]:
            return (
                rutter_public.TransitionHook(
                    "prompt-check",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter_constructor=child_charter,
                ),
            )

    path = Path("prompt-reopen.reckoning.json")
    registry = rutter_public.RutterRegistry({"parent": PromptHookParent}, tmp_path)
    voyage = registry.create("parent", path, {})
    prompt = voyage.get_status().instruction
    assert prompt is not None

    attached_child = voyage.advance(
        {"outcome": "approved"},
        responding_to=prompt.evolution_entry_id,
        continue_=False,
    )
    assert attached_child.rutter_id == HookChild.rutter_id
    assert len(seen) == 1
    assert isinstance(seen[0], Transition)
    assert (
        seen[0].source,
        seen[0].outcome,
        seen[0].target,
        seen[0].transition_id,
    ) == ("review", "approved", "publish", seen[0].transition_id)
    active_child = voyage._store.read().root.active_child
    assert active_child is not None
    assert active_child.run.charter == rutter_public.Charter(
        {
            "source": "review",
            "outcome": "approved",
            "target": "publish",
            "transition_id": seen[0].transition_id,
        }
    )
    reopened = registry.open(path)
    settled_child = reopened.advance(continue_=False)
    assert settled_child.condition == "terminal"
    reopened = registry.open(path)

    class SimulatedCrash(Exception):
        pass

    original_replace = reopened._store.replace

    def crash_after_persist(previous: object, replacement: object) -> None:
        original_replace(previous, replacement)
        raise SimulatedCrash

    with monkeypatch.context() as patcher:
        patcher.setattr(reopened._store, "replace", crash_after_persist)
        with pytest.raises(SimulatedCrash):
            reopened.advance(continue_=False)

    returned = registry.open(path)
    persisted = returned._store.read()
    assert persisted.root.entered_evolution.evolution_id == "review"
    assert persisted.root.active_child is None
    history = rutter_public.HistoryView(
        persisted.root.history, persisted.completed_runs
    )
    assert len(history.turns("review")) == 1
    assert tuple(
        call.transition_hook_id
        for call in history.subrutters(transition_hook_id="prompt-check")
    ) == (
        "prompt-check",
    )

    before = (tmp_path / path).read_bytes()
    assert returned.get_status().current_evolution.evolution_id == "review"
    assert returned.get_status().instruction is None
    with pytest.raises(rutter_public.NotApplicable):
        returned.validate(
            {"revision": 0, "outcome": "approved", "evidence": {}}
        )
    assert (tmp_path / path).read_bytes() == before

    target = returned.advance(continue_=False)
    assert target.evolution_id == "publish"
    assert target.condition == "ready"


def test_pure_action_attachment_return_does_not_offer_or_replay_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A returned hook must expose only continuation from an accepted MachineStep."""

    executions: list[str] = []

    def approve(context: rutter_public.MachineContext) -> rutter_public.MachineResult:
        executions.append(context.machine_id)
        return rutter_public.MachineResult("approved", {})

    class ActionHookParent(rutter_public.Rutter):
        rutter_id = "action-hook-parent"
        definition_version = 1
        initial_evolution_id = "review"

        def define_evolutions(self) -> dict[str, object]:
            return {
                "review": rutter_public.MachineStep(
                    approve,
                    mode="pure",
                    next_on_outcome="publish",
                ),
                "publish": rutter_public.Terminal(result=rutter_public.VoyageResult("finished", {})
                ),
            }

        def define_transition_hooks(self) -> tuple[object, ...]:
            return (
                rutter_public.TransitionHook(
                    "action-check",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter_constructor=lambda context: {
                        "source": context.transition.source
                    },
                ),
            )

    path = Path("action-reopen.reckoning.json")
    registry = rutter_public.RutterRegistry({"parent": ActionHookParent}, tmp_path)
    voyage = registry.create("parent", path, {})

    attached_child = voyage.advance(continue_=False)
    assert attached_child.rutter_id == HookChild.rutter_id
    assert len(executions) == 1
    accepted_action_id = executions[0]
    reopened = registry.open(path)
    settled_child = reopened.advance(continue_=False)
    assert settled_child.condition == "terminal"
    reopened = registry.open(path)

    class SimulatedCrash(Exception):
        pass

    original_replace = reopened._store.replace

    def crash_after_persist(previous: object, replacement: object) -> None:
        original_replace(previous, replacement)
        raise SimulatedCrash

    with monkeypatch.context() as patcher:
        patcher.setattr(reopened._store, "replace", crash_after_persist)
        with pytest.raises(SimulatedCrash):
            reopened.advance(continue_=False)

    returned = registry.open(path)
    before = (tmp_path / path).read_bytes()
    assert returned.get_status().current_evolution.evolution_id == "review"
    assert returned.get_status().instruction is None
    with pytest.raises(rutter_public.NotApplicable):
        returned.validate(rutter_public.MachineResult("approved", {}))
    assert executions == [accepted_action_id]
    assert (tmp_path / path).read_bytes() == before

    target = returned.advance(continue_=False)
    assert target.evolution_id == "publish"
    assert target.condition == "ready"
    assert executions == [accepted_action_id]
