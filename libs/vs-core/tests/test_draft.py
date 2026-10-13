"""The builders every strategy uses emit protocol-correct values from the view alone."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from vs_core.api import (
    Access,
    ChargeClass,
    ContinuationId,
    CoreView,
    Draft,
    IdScheme,
    InvocationId,
    InvocationRef,
    SchemaRef,
    SessionId,
    TurnChain,
    TurnShape,
    Value,
    attempt_scope,
    initial_state,
    invocation_for,
    project,
    request_turn,
    run_scope,
    session_for,
)
from vs_core.api import AttemptId as Attempt

SCHEME = IdScheme("t", role_prefix="role-")
SCHEMA = SchemaRef(name="reply", version=1)


class Note(Value):
    text: str


def _view() -> CoreView:
    return project(initial_state())


def _shape() -> TurnShape:
    scope = run_scope(_view())
    return TurnShape(
        role="judge",
        subject="the run/1",
        workspace=scope,
        access=Access.READ_ONLY,
        output_schema=SCHEMA,
    )


def _ref(name: str) -> InvocationRef:
    return InvocationRef(
        session_id=SessionId(root="s"), invocation_id=InvocationId(root=name), generation=0
    )


@given(charge=st.sampled_from(["free", "paid", "correction"]), serial=st.integers(0, 9))
def test_a_turn_names_its_predecessor_and_a_fresh_session_identity(
    charge: ChargeClass, serial: int
) -> None:
    chain = TurnChain[Note](charge=charge, serial=serial, invocation=_ref("before"))
    decision = request_turn(SCHEME, _shape(), chain, run_scope(_view()))
    turn = decision.turn
    assert turn.predecessor == _ref("before")
    assert turn.charge_class == charge
    assert turn.invocation_id == SCHEME.invocation_id("judge", "the run/1", serial)
    assert decision.decision_id == SCHEME.decision_id("turn", "the run/1", serial)
    assert turn.session.policy == "reuse"
    assert turn.session.role_id.root == "role-judge"


def test_a_resume_names_its_continuation_and_the_invocation_core_authorized() -> None:
    chain = TurnChain[Note](
        charge="resume",
        serial=3,
        invocation=_ref("before"),
        resume_as=InvocationId(root="authorized"),
        continuation=ContinuationId(root="cont"),
    )
    decision = request_turn(SCHEME, _shape(), chain, run_scope(_view()))
    assert decision.turn.predecessor is None
    assert decision.turn.continuation_id == ContinuationId(root="cont")
    assert decision.turn.invocation_id == InvocationId(root="authorized")
    assert invocation_for(SCHEME, "judge", "x", chain) == InvocationId(root="authorized")


def test_a_session_is_always_declared_reusable() -> None:
    spec = session_for(SCHEME, "implementer", "work 1", Access.WRITE_CANDIDATE)
    assert (spec.policy, spec.lifetime) == ("reuse", "owner")
    assert spec.session_id == SCHEME.session_id("implementer", "work 1")


@given(untils=st.lists(st.one_of(st.none(), st.floats(0, 100)), max_size=5))
def test_the_earliest_held_back_turn_sets_the_wake_time(untils: list[float | None]) -> None:
    view = _view()
    draft = Draft(view=view, state=view)
    now = view.run.now_at
    due = [draft.due(TurnChain[Note](ask_not_before=until)) for until in untils]
    assert due == [until is None or now >= until for until in untils]
    waiting = [until for until in untils if until is not None and until > now]
    assert draft.wake_at == (min(waiting) if waiting else None)


def test_an_attempt_scope_reports_the_live_generation_and_a_new_attempt_the_given_one() -> None:
    view = _view()
    assert attempt_scope(view, Attempt(root="new"), 7).generation == 7
