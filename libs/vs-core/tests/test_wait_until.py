"""A wait states how long it may take; core ends it at that limit or when a watch fires.

Properties over the public API with the pure core and no real time: the clock is the
``ClockAdvanced`` event, so a test moves time by stepping it.
"""

from typing import ClassVar, Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

import vs_core.api as core

LIMITS = st.floats(min_value=0.5, max_value=200.0, allow_nan=False)
RUN_DEADLINE = 10_000.0


class Written(core.Value):
    done: bool


class Write(core.OperationRequest):
    kind: Literal["test.until.write"] = "test.until.write"
    lifecycle: Literal[core.LifecycleClass.IDEMPOTENT_WRITE] = core.LifecycleClass.IDEMPOTENT_WRITE
    outcome_model: ClassVar[type[BaseModel]] = Written


def _codec() -> core.OperationRegistry:
    descriptor = core.OperationDescriptor(
        kind="test.until.write",
        lifecycle=core.LifecycleClass.IDEMPOTENT_WRITE,
        request_schema=core.SchemaRef(name="write", version=1),
        outcome_schema=core.SchemaRef(name="written", version=1),
    )
    return core.OperationRegistry(
        (
            core.OperationRegistration(
                descriptor=descriptor, request_model=Write, outcome_model=Written
            ),
        )
    )


def _fresh() -> tuple[core.CoreState, core.OperationRegistry]:
    codec = _codec()
    state = core.initial_state()
    state = state.model_copy(
        update={
            "registry": codec.descriptors,
            "run": state.run.model_copy(
                update={
                    "deadline_at": RUN_DEADLINE,
                    "capabilities": core.Capabilities(operations=codec.descriptors),
                }
            ),
        }
    )
    return state, codec


def _wait(codec: core.OperationRegistry, name: str, until: core.Within) -> core.Wait:
    work = codec.validate_decision(
        core.Operation(
            decision_id=core.DecisionId(root=name),
            scope=core.Scope(owner=core.initial_state().run.run_id, generation=0),
            request=Write(),
        )
    )
    return core.Wait(request=work, until=until)


def _submit(state: core.CoreState, *waits: core.Wait) -> core.Transition:
    return core.step(
        state, core.ProposalSubmitted(decisions=waits, expected_revision=state.revision)
    )


def _dispatch_all(state: core.CoreState) -> core.CoreState:
    for record in state.records.records:
        if record.phase is core.RequestPhase.PREPARED:
            state = core.step(state, core.DispatchAuthorized(request_id=record.request_id)).state
    return state


def _operations(state: core.CoreState) -> list[core.RequestRecord]:
    return [
        r for r in state.records.records if isinstance(r.request, core.ExecuteRegisteredOperation)
    ]


def _open(state: core.CoreState) -> list[core.RequestRecord]:
    return [r for r in _operations(state) if r.phase is not core.RequestPhase.BLOCKED]


def _failures(transition: core.Transition) -> list[core.Failed]:
    return [e for e in transition.events if isinstance(e, core.Failed)]


@given(limit=LIMITS, later=st.floats(min_value=0.0, max_value=50.0, allow_nan=False))
def test_a_wait_past_its_limit_ends_with_timed_out(limit: float, later: float) -> None:
    state, codec = _fresh()
    state = _dispatch_all(_submit(state, _wait(codec, "w", core.Within(limit=limit))).state)
    (record,) = _operations(state)
    assert record.until.at == pytest.approx(limit)

    just_before = core.step(state, core.ClockAdvanced(now_at=limit * 0.99))
    assert _failures(just_before) == []
    assert [r.phase for r in _open(just_before.state)] != []

    ended = core.step(state, core.ClockAdvanced(now_at=limit + later))
    (failure,) = _failures(ended)
    assert failure.reason is core.FailureReason.TIMED_OUT
    assert failure.decision == core.DecisionId(root="w")
    (done,) = _operations(ended.state)
    assert done.phase is core.RequestPhase.BLOCKED
    assert done.failure == failure


@given(limits=st.lists(LIMITS, min_size=1, max_size=5))
def test_no_wait_stays_open_past_the_latest_limit(limits: list[float]) -> None:
    state, codec = _fresh()
    waits = [_wait(codec, f"w{i}", core.Within(limit=limit)) for i, limit in enumerate(limits)]
    state = _dispatch_all(_submit(state, *waits).state)
    latest = max(r.until.at for r in _operations(state))
    assert latest == pytest.approx(max(limits))
    # Time passes in steps; at the latest limit nothing canonical is still open.
    for moment in sorted({*(r.until.at for r in _operations(state)), latest}):
        state = core.step(state, core.ClockAdvanced(now_at=moment)).state
    assert _open(state) == []


@given(limit=st.floats(min_value=RUN_DEADLINE, max_value=RUN_DEADLINE * 3, allow_nan=False))
def test_a_limit_is_capped_at_the_run_deadline(limit: float) -> None:
    state, codec = _fresh()
    state = _submit(state, _wait(codec, "w", core.Within(limit=limit))).state
    (record,) = _operations(state)
    assert record.until.at == RUN_DEADLINE


@given(limit=st.floats(max_value=0.0, allow_nan=False, allow_infinity=False))
def test_a_non_positive_limit_is_refused(limit: float) -> None:
    state, codec = _fresh()
    result = _submit(state, _wait(codec, "w", core.Within(limit=limit)))
    assert _operations(result.state) == []
    rejected = [e for e in result.events if isinstance(e, core.Rejected)]
    assert [e.decision_id for e in rejected] == [core.DecisionId(root="w")]


def _closed_workspace() -> core.WhileOpen:
    """A workspace core has no open attempt for: its watch has already fired."""
    return core.WhileOpen(
        workspace=core.AttemptRef(attempt_id=core.AttemptId(root="never-opened"), generation=0)
    )


@given(
    declared=st.lists(st.booleans(), min_size=1, max_size=6),
    limit=LIMITS,
)
def test_a_fired_watch_ends_exactly_the_waits_that_declared_it(
    declared: list[bool], limit: float
) -> None:
    state, codec = _fresh()
    waits = [
        _wait(
            codec,
            f"w{i}",
            core.Within(limit=limit, watches=(_closed_workspace(),) if watched else ()),
        )
        for i, watched in enumerate(declared)
    ]
    result = _submit(state, *waits)
    failures = {f.decision: f for f in _failures(result)}
    expected = {core.DecisionId(root=f"w{i}") for i, watched in enumerate(declared) if watched}
    assert set(failures) == expected
    assert all(f.reason is core.FailureReason.WORKSPACE_CLOSED for f in failures.values())
    assert all(f.watch == _closed_workspace() for f in failures.values())
    still_open = {r.request.decision_id for r in _open(result.state)}
    assert still_open == {core.DecisionId(root=f"w{i}") for i in range(len(declared))} - expected


def test_a_watch_alone_is_not_an_until() -> None:
    _, codec = _fresh()
    work = _wait(codec, "w", core.Within(limit=10.0)).request
    bad = core.Wait.model_construct(request=work, until=_closed_workspace())
    with pytest.raises(core.ContractError, match="until"):
        core.check_decisions(core.initial_state().run.declaration, [bad])


def _with_open_workspace(state: core.CoreState) -> tuple[core.CoreState, core.AttemptRef]:
    view = core.AttemptView(
        attempt_id=core.AttemptId(root="open"),
        item_id=core.ItemId(root="item"),
        generation=0,
        phase=core.AttemptPhase.ACTIVE,
        workspace=core.WorkspacePlan(
            mode=core.WorkspaceMode.EXCLUSIVE_ROOT, base=state.run.facts.baseline
        ),
        budget=core.AttemptBudget(),
        admission_id=core.DecisionId(root="admission"),
    )
    opened = state.model_copy(update={"attempts": core.AttemptsState(attempts=(view,))})
    return opened, core.AttemptRef(attempt_id=view.attempt_id, generation=0)


@given(limit=LIMITS)
def test_a_watch_whose_condition_holds_does_not_end_the_wait(limit: float) -> None:
    state, codec = _fresh()
    state, workspace = _with_open_workspace(state)
    watches = (
        core.WhileOpen(workspace=workspace),
        core.WhileAlive(job=core.ResourceId(root="job-not-yet-registered")),
    )
    result = _submit(state, _wait(codec, "w", core.Within(limit=limit) | watches[0] | watches[1]))
    assert _failures(result) == []
    (record,) = _open(result.state)
    assert record.until.watches == watches
