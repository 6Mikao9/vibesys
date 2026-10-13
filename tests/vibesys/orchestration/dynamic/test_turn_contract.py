"""What dynamic asks of core and answers back, in ways the flow stories do not already pin.

Core owns the rules of a turn (how often a reply is corrected, what an unrecorded wait is, how an
adoption is retried). These tests pin the dynamic half: which role may be corrected and in which
session, that every turn it asks carries a template of its own role and a typed context, that a
reply waiting for something nothing recorded ends the turn and not the run, and that a winner core
gives up adopting ends the run as a failure with no selection.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import (
    FACTS,
    PLANNER_LABEL,
    Host,
    assert_flow_closed,
    begin,
    config,
)
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vibesys.orchestration.dynamic.models import ImplementerResult, ReviewResult
from vibesys.orchestration.dynamic.search.api import (
    IMPLEMENTER,
    JUDGE,
    ROLES,
    ImplementPrompt,
    PlannerCorrection,
    PlannerPrompt,
    ProfilePrompt,
    ReviewPrompt,
    dynamic,
)
from vs_core.api import (
    AdoptionFailureReason,
    Finish,
    ProposeWinner,
    RequestTurn,
    Retained,
    RevisionRef,
    StartAttempt,
    Wait,
)
from vs_core.api.search import ReplyCorrection, WaitUnrecorded
from vs_core.api.testing import FlowRun

MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])
CANDIDATE = RevisionRef.of_git_commit("c1")
NOT_JSON = "not json"


def to_the_turn_of(host: Host, role: str) -> None:
    """Plan one hypothesis, open its workspace and (for the judge) let the implementer finish."""
    host.drain_until_turn(PLANNER_LABEL)
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(implement("h1")))
    host.answer(host.run.expect(StartAttempt, label="start"))
    if role == "judge":
        result = ImplementerResult.model_validate_json(implemented())
        host.run.reply(
            host.run.expect_turn(role="implementer"),
            result,
            checkpoint=Retained(revision=CANDIDATE),
        )


# -- a winner core gives up adopting ---------------------------------------------------------------


@MODES
@given(reason=st.sampled_from(list(AdoptionFailureReason)))
@settings(max_examples=len(AdoptionFailureReason), deadline=None, derandomize=True, database=None)
def test_an_adoption_core_gives_up_on_ends_the_run_in_failure_without_a_selection(
    *, restarting: bool, reason: AdoptionFailureReason
) -> None:
    """Regression: `AdoptionFailed` was ignored, so the run once waited in ADOPTING for ever."""
    cfg = config()
    run = FlowRun.start(dynamic, cfg, facts=FACTS, restarting=restarting, adopting=False)
    host = Host(run, cfg)
    to_the_turn_of(host, "judge")
    verdict = ReviewResult.model_validate_json(reviewed())
    run.reply(run.expect_turn(role="judge"), verdict)
    while not host.open(ProposeWinner):
        assert host.step(0), run.timeline()
    proposal = run.expect(ProposeWinner)
    assert isinstance(proposal.request, ProposeWinner)
    assert proposal.request.selection.kind == "retained_candidate"

    run.refuse_adoption(proposal, reason)
    host.drain()

    finish = run.finished
    assert isinstance(finish, Finish)
    assert finish.result.outcome == "failure"
    assert finish.result.selection is None
    assert finish.result.reason == f"adoption-failed: {reason.value}"
    assert_flow_closed(host)


# -- a reply that waits for something nothing recorded ----------------------------------------------


def wait_on(kind: str) -> str:
    return json.dumps({"kind": kind, "handles": ["eval_1"]})


@MODES
@pytest.mark.parametrize("role", ["implementer", "judge"])
@pytest.mark.parametrize("kind", ["waiting_for_profiler", "waiting_for_evaluation", "unknown_wait"])
def test_a_wait_the_run_never_recorded_ends_its_turn_whichever_wait_it_names(
    *, restarting: bool, role: str, kind: str
) -> None:
    """Regression: a `waiting_for_profiler` reply, valid under the implementer schema, crashed the run.

    A wait nothing recorded (these turns never submitted an evaluation) or a kind the schema
    rejects is told to the agent once, as the exchange of its kind; the agent may then answer
    properly and the run ends with everything closed.
    """
    host = begin(config(), restarting=restarting)
    to_the_turn_of(host, role)
    asked = host.run.expect_turn(role=role)
    host.run.reply_raw(asked, wait_on(kind))

    follow = host.run.expect_turn(role=role)
    unrecorded = kind == "waiting_for_evaluation"
    exchanges = IMPLEMENTER.exchanges if role == "implementer" else JUDGE.exchanges
    assert follow.template == (exchanges.wait_unrecorded if unrecorded else exchanges.correction)
    assert isinstance(follow.context, WaitUnrecorded if unrecorded else ReplyCorrection)
    assert isinstance(follow.request, RequestTurn)
    assert follow.request.turn.charge_class == "correction"
    assert follow.request.turn.session == asked.request.turn.session  # ty: ignore[unresolved-attribute]

    if role == "implementer":
        result = ImplementerResult.model_validate_json(implemented())
        host.run.reply(follow, result, checkpoint=Retained(revision=CANDIDATE))
        host.run.reply(
            host.run.expect_turn(role="judge"), ReviewResult.model_validate_json(reviewed())
        )
    else:
        host.run.reply(follow, ReviewResult.model_validate_json(reviewed()))
    host.drain()

    assert isinstance(host.run.finished, Finish)
    assert_flow_closed(host)


# -- every turn the search asks ----------------------------------------------------------------------

PROMPTS = {role.name: role.prompt for role in ROLES}
EXCHANGE_TEMPLATES = {
    name
    for role in ROLES
    for name in (role.exchanges.correction, role.exchanges.resume, role.exchanges.wait_unrecorded)
    if name is not None
}
TURN_CONTEXTS = (
    PlannerPrompt,
    PlannerCorrection,
    ImplementPrompt,
    ReviewPrompt,
    ProfilePrompt,
    ReplyCorrection,
    WaitUnrecorded,
)
STEPS = st.lists(st.tuples(st.integers(0, 7), st.integers(0, 11)), max_size=40)


def check_asked_turns(host: Host) -> None:
    """Every open turn carries a template of the search's roles and a typed context."""
    for asked in host.run.open:
        if isinstance(asked.request, RequestTurn):
            assert asked.template in {*PROMPTS.values(), *EXCHANGE_TEMPLATES}, asked
            assert isinstance(asked.context, TURN_CONTEXTS)


@MODES
@settings(max_examples=40, deadline=None, derandomize=True, database=None)
@given(steps=STEPS, in_flight=st.integers(1, 2))
def test_every_turn_is_asked_with_its_roles_template_or_an_exchange_and_a_session_that_never_changes(
    *, restarting: bool, steps: list[tuple[int, int]], in_flight: int
) -> None:
    host = begin(config(max_rounds=2, max_in_flight=in_flight), restarting=restarting)
    for pick, variant in steps:
        check_asked_turns(host)
        if not host.step(pick, variant):
            break
    while True:
        check_asked_turns(host)
        if not host.step(0):
            break

    seen: dict[str, object] = {}
    for item in host.run.decisions:
        if isinstance(item, Wait) and isinstance(item.request, RequestTurn):
            turn = item.request.turn
            assert seen.setdefault(turn.session.session_id.root, turn.session) == turn.session
            if turn.charge_class == "correction":
                assert turn.predecessor is not None
                assert turn.predecessor.session_id == turn.session.session_id


@MODES
@pytest.mark.parametrize("role", [PLANNER_LABEL, "implementer", "judge"])
def test_an_invalid_reply_is_corrected_in_the_session_of_the_turn_it_corrects(
    *, restarting: bool, role: str
) -> None:
    host = begin(config(), restarting=restarting)
    if role == PLANNER_LABEL:
        host.drain_until_turn(PLANNER_LABEL)
    else:
        to_the_turn_of(host, role)
    asked = host.run.expect_turn(role=role)
    host.run.reply_raw(asked, NOT_JSON)

    follow = host.run.expect_turn(role=role)
    original, corrected = asked.request.turn, follow.request.turn  # ty: ignore[unresolved-attribute]
    assert corrected.charge_class == "correction"
    assert corrected.session == original.session
    assert corrected.predecessor is not None
    assert corrected.predecessor.invocation_id == original.invocation_id
