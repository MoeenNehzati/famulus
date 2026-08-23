"""Specify record-derived transition hooks without reducer phases."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

import officina.rutter as rutter_public
from officina.rutter.engine import Edge


class HookChild(rutter_public.Rutter):
    rutter_id = "hook-child"
    definition_version = 1
    start_state = "done"

    def define_states(self) -> dict[str, object]:
        return {"done": rutter_public.Done(rutter_public.RunResult("checked", {}))}


def test_edge_match_constructors_cover_wildcard_and_exact_edges() -> None:
    """Changing a matcher field or treating a wildcard as exact must fail."""

    assert hasattr(rutter_public, "EdgeMatch")
    assert hasattr(rutter_public, "after")
    assert hasattr(rutter_public, "before")
    assert hasattr(rutter_public, "on_edge")
    after = rutter_public.after
    before = rutter_public.before
    on_edge = rutter_public.on_edge
    edge = Edge("record-review", "entry-review", "review", "approved", "publish")

    assert after("review").matches(edge)
    assert before("publish").matches(edge)
    assert on_edge(outcome="approved").matches(edge)
    assert on_edge(
        source="review", outcome="approved", target="publish"
    ).matches(edge)
    assert not after("draft").matches(edge)
    assert not before("archive").matches(edge)
    assert not on_edge(outcome="rejected").matches(edge)
    with pytest.raises(FrozenInstanceError):
        after("review").source = "draft"


def test_case_maker_constructor_freezes_one_validated_hook_definition() -> None:
    """Accepting an unstable ID, matcher, child, or Charter callback must fail."""

    assert hasattr(rutter_public, "CaseMaker")
    maker = rutter_public.CaseMaker(
        "review-check",
        on=rutter_public.after("review"),
        child=HookChild,
        charter=lambda context: {"source": context.state.state_id},
    )

    assert maker.id == "review-check"
    assert maker.on == rutter_public.EdgeMatch(source="review")
    assert maker.child is HookChild
    with pytest.raises(FrozenInstanceError):
        maker.id = "other"
    with pytest.raises(rutter_public.RutterDefinitionError):
        rutter_public.CaseMaker(
            "bad id",
            on=rutter_public.after("review"),
            child=HookChild,
            charter=lambda context: {},
        )


def _completed(run_id: str) -> rutter_public.CompletedRun:
    result = rutter_public.RunResult("checked", {"run": run_id})
    return rutter_public.CompletedRun(
        run_id,
        "hook-child",
        1,
        rutter_public.Charter({}),
        (
            rutter_public.DoneRecord(
                f"done-{run_id}",
                f"entry-{run_id}",
                "done",
                result,
            ),
        ),
    )


def test_attached_calls_filters_exact_provenance_not_colliding_explicit_sites() -> None:
    """Filtering calls by site alone must not treat an explicit Call as a hook."""

    source = rutter_public.ActionRecord(
        "edge-review",
        "action-review",
        "entry-review",
        "review",
        "pure",
        rutter_public.ActionResult("approved", {}),
    )
    explicit = rutter_public.CallRecord(
        "call-explicit",
        "entry-review",
        "explicit_call",
        "review-check",
        None,
        "run-explicit",
    )
    attached = rutter_public.CallRecord(
        "call-attached",
        "entry-review",
        "attached_case",
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

    assert len(history.calls("review-check")) == 2
    assert history.attached_calls() == (history.calls("review-check")[1],)
    assert history.attached_calls(case_maker_id="review-check") == (
        history.calls("review-check")[1],
    )
    assert history.attached_calls(edge_id="edge-review") == (
        history.calls("review-check")[1],
    )
    assert history.attached_calls(case_maker_id="other") == ()
    assert history.attached_calls(edge_id="edge-other") == ()


def test_every_matcher_reuses_its_record_anchored_context_and_provenance(
    tmp_path: Path,
) -> None:
    """Rebuilding a hook edge from later attached calls must not shift its context."""

    seen: dict[str, list[rutter_public.EdgeContext]] = {}

    def remember(maker_id: str):
        def build(context: rutter_public.EdgeContext) -> dict[str, object]:
            seen.setdefault(maker_id, []).append(context)
            return {"maker": maker_id}

        return build

    class HookedParent(rutter_public.Rutter):
        rutter_id = "hooked-parent"
        definition_version = 1
        start_state = "review"
        allow_multiple_cases_at_once = True

        def define_states(self) -> dict[str, object]:
            return {
                "review": rutter_public.Action(
                    lambda context: rutter_public.ActionResult("approved", {}),
                    mode="pure",
                    then="invoke",
                ),
                "invoke": rutter_public.Call(
                    HookChild,
                    charter=lambda context: {"kind": "explicit"},
                    then="done",
                ),
                "done": rutter_public.Done(
                    rutter_public.RunResult("finished", {"ok": True})
                ),
            }

        def define_case_makers(self) -> tuple[object, ...]:
            return (
                rutter_public.CaseMaker(
                    "after-review",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter=remember("after-review"),
                ),
                rutter_public.CaseMaker(
                    "before-invoke",
                    on=rutter_public.before("invoke"),
                    child=HookChild,
                    charter=remember("before-invoke"),
                ),
                rutter_public.CaseMaker(
                    "exact-review",
                    on=rutter_public.on_edge(
                        source="review", outcome="approved", target="invoke"
                    ),
                    child=HookChild,
                    charter=remember("exact-review"),
                ),
                rutter_public.CaseMaker(
                    "post-call",
                    on=rutter_public.after("invoke"),
                    child=HookChild,
                    charter=remember("post-call"),
                ),
                rutter_public.CaseMaker(
                    "post-done",
                    on=rutter_public.after("done"),
                    child=HookChild,
                    charter=remember("post-done"),
                ),
            )

    voyage = rutter_public.RutterRegistry(
        {"parent": HookedParent}, tmp_path
    ).create("parent", Path("hooked.reckoning.json"), {})

    terminal = voyage.next(continue_=True)

    assert terminal.state_id == "done"
    assert terminal.condition == "terminal"
    reckoning = voyage._store.read()
    history = rutter_public.HistoryView(
        reckoning.root.history, reckoning.completed_runs
    )
    attached = history.attached_calls()
    assert tuple(call.site for call in attached) == (
        "after-review",
        "before-invoke",
        "exact-review",
        "post-call",
        "post-done",
    )
    source_by_edge = {
        (entry.call_id if isinstance(entry, rutter_public.CallRecord) else entry.record_id): entry
        for entry in reckoning.root.history
        if not (
            isinstance(entry, rutter_public.CallRecord)
            and entry.site_kind == "attached_case"
        )
    }
    for call in attached:
        assert call.attached_to_edge_id in source_by_edge
        source = source_by_edge[call.attached_to_edge_id]
        assert all(context.record == source for context in seen[call.site])
        assert all(
            context.state.history.entries()
            == history.strict_prefix(source).entries()
            for context in seen[call.site]
        )
        assert all(
            context.edge["edge_id"] == call.attached_to_edge_id
            for context in seen[call.site]
        )


def test_multiple_selection_faults_with_every_maker_before_child_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Starting one child or omitting a selected ID before policy checks must fail."""

    selected_in_order: list[str] = []

    def choose(maker_id: str, value: dict[str, object] | None):
        def build(context: rutter_public.EdgeContext):
            del context
            selected_in_order.append(maker_id)
            return value

        return build

    class SingleCaseParent(rutter_public.Rutter):
        rutter_id = "single-case-parent"
        definition_version = 1
        start_state = "review"

        def define_states(self) -> dict[str, object]:
            return {
                "review": rutter_public.Action(
                    lambda context: rutter_public.ActionResult("approved", {}),
                    mode="pure",
                    then="done",
                ),
                "done": rutter_public.Done(rutter_public.RunResult("finished", {})),
            }

        def define_case_makers(self) -> tuple[object, ...]:
            return (
                rutter_public.CaseMaker(
                    "declined",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter=choose("declined", None),
                ),
                rutter_public.CaseMaker(
                    "first",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter=choose("first", {"case": "first"}),
                ),
                rutter_public.CaseMaker(
                    "second",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter=choose("second", {"case": "second"}),
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

    fault = voyage.next(continue_=True)

    assert fault.condition == "fault"
    persisted = voyage._store.read()
    assert persisted.fault == {
        "category": "case-cardinality",
        "run_id": persisted.root.run_id,
        "state_id": "review",
        "node_entry_id": persisted.root.entered_node.entry_id,
        "case_maker_ids": ("first", "second"),
    }
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

    def charter(context: rutter_public.EdgeContext) -> dict[str, object]:
        del context
        if failure == "charter":
            raise RuntimeError("private Charter detail")
        return {}

    class FailingCaseParent(rutter_public.Rutter):
        rutter_id = f"failing-case-{failure}"
        definition_version = 1
        start_state = "review"

        def define_states(self) -> dict[str, object]:
            return {
                "review": rutter_public.Action(
                    lambda context: rutter_public.ActionResult("approved", {}),
                    mode="pure",
                    then="done",
                ),
                "done": rutter_public.Done(rutter_public.RunResult("finished", {})),
            }

        def define_case_makers(self) -> tuple[object, ...]:
            return (
                rutter_public.CaseMaker(
                    "failing-case",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter=charter,
                ),
            )

    voyage = rutter_public.RutterRegistry(
        {"parent": FailingCaseParent}, tmp_path
    ).create("parent", Path(f"{failure}.reckoning.json"), {})
    if failure == "matcher":
        def fail_match(self: object, edge: object) -> bool:
            del self, edge
            raise RuntimeError("private matcher detail")

        monkeypatch.setattr(rutter_public.EdgeMatch, "matches", fail_match)

    fault = voyage.next(continue_=True)

    assert fault.condition == "fault"
    persisted = voyage._store.read()
    assert persisted.fault == {
        "category": category,
        "run_id": persisted.root.run_id,
        "state_id": "review",
        "node_entry_id": persisted.root.entered_node.entry_id,
        "case_maker_ids": ("failing-case",),
    }
    assert len(persisted.root.history) == 1
    assert isinstance(persisted.root.history[0], rutter_public.ActionRecord)
    assert persisted.root.active_child is None
    assert persisted.completed_runs == {}


def test_prompt_attachment_reopens_after_attach_settle_and_return_boundaries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A returned hook must remain resumable without replaying the accepted Prompt."""

    class PromptHookParent(rutter_public.Rutter):
        rutter_id = "prompt-hook-parent"
        definition_version = 1
        start_state = "review"

        def define_states(self) -> dict[str, object]:
            return {
                "review": rutter_public.Prompt(
                    "Review.",
                    answer=rutter_public.AnswerSpec({"approved": {}}),
                    then="publish",
                ),
                "publish": rutter_public.Done(
                    rutter_public.RunResult("finished", {})
                ),
            }

        def define_case_makers(self) -> tuple[object, ...]:
            return (
                rutter_public.CaseMaker(
                    "prompt-check",
                    on=rutter_public.after("review"),
                    child=HookChild,
                    charter=lambda context: {"source": context.edge["source"]},
                ),
            )

    path = Path("prompt-reopen.reckoning.json")
    registry = rutter_public.RutterRegistry({"parent": PromptHookParent}, tmp_path)
    voyage = registry.create("parent", path, {})

    attached_child = voyage.next(
        {"revision": 0, "outcome": "approved", "evidence": {}},
        continue_=False,
    )
    assert attached_child.rutter_id == HookChild.rutter_id
    reopened = registry.open(path)
    settled_child = reopened.next(continue_=False)
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
            reopened.next(continue_=False)

    returned = registry.open(path)
    persisted = returned._store.read()
    assert persisted.root.entered_node.state_id == "review"
    assert persisted.root.active_child is None
    history = rutter_public.HistoryView(
        persisted.root.history, persisted.completed_runs
    )
    assert len(history.turns("review")) == 1
    assert tuple(call.site for call in history.attached_calls()) == (
        "prompt-check",
    )

    target = returned.next(continue_=False)
    assert target.state_id == "publish"
    assert target.condition == "ready"
