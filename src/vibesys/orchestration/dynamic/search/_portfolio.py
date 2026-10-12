"""One planning call: ask for a portfolio, correct it, and schedule what is valid.

The planner is a run-scoped, free agent. Its reply arrives already parsed against the reply
model; this machine validates it against what the search has recorded (`validate`), asks for a
correction up to `max_corrections` times, and when the corrections do not fix the plan keeps the
best valid outcome: a held underfilled plan, else the valid part of the last reply, else a fresh
question, else a typed give-up. A lost turn is asked again after a backoff on the run clock.

The agent's own retries are off (`Retry.none()`): every ask carries a prompt built from the
state at that moment, and every correction names the problem as typed data.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from vibesys.orchestration.dynamic.models import PortfolioPlan
from vibesys.orchestration.dynamic.search._plan import PlanCheck, validate
from vibesys.orchestration.dynamic.search._prompts import PlannerCorrection, PlannerReask
from vibesys.orchestration.dynamic.search._values import (
    PlanProblem,
    ProblemKind,
    Role,
)
from vs_core.api import (
    THIS_RUN,
    AskAgent,
    DecisionId,
    Failed,
    FailureReason,
    InvocationRef,
    RunView,
    Seconds,
    TimeReached,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Wait,
    WakeAt,
    Within,
)

if TYPE_CHECKING:
    from vibesys.orchestration.dynamic.search._context import PlanWorld
    from vs_core.api import Decision, Event


class Ask(Value):
    """The facts of one planning call, which stay the same across its asks."""

    capacity: int = Field(ge=0)
    """The slots that were free when the call began; a reply should fill them."""
    asks: int = Field(default=0, ge=0)
    """Asks sent so far; the next ask is numbered by it."""
    corrections: int = Field(default=0, ge=0)
    drops: int = Field(default=0, ge=0)
    fresh: int = Field(default=0, ge=0)
    held: PortfolioPlan | None = None
    """The first valid plan that left slots free, kept in case a correction does not improve it."""
    problem: PlanProblem | None = None
    """Why the last reply was not taken; the next prompt shows it."""


class Asking(Value):
    """A planner turn is out."""

    kind: Literal["asking"] = "asking"
    ask: Ask


class BackingOff(Value):
    """A lost turn waits out its backoff before the question is asked again."""

    kind: Literal["backing_off"] = "backing_off"
    ask: Ask
    until: Seconds
    lost: InvocationRef


class Scheduled(Value):
    """The call ended with a plan to schedule."""

    kind: Literal["scheduled"] = "scheduled"
    check: PlanCheck


class GaveUp(Value):
    """The call ended with nothing valid.

    ``stuck`` says nothing is in flight to wait for, so planning has failed for good;
    ``blocked_at_done`` is the number of finished workstreams planning waits to exceed.
    """

    kind: Literal["gave_up"] = "gave_up"
    problem: PlanProblem | None
    stuck: bool
    blocked_at_done: int = Field(ge=0)


type Planning = Annotated[Asking | BackingOff | Scheduled | GaveUp, Field(discriminator="kind")]


def _ask(world: PlanWorld, ask: Ask, *, of: InvocationRef | None) -> tuple[Ask, Decision]:
    """The next question of the call, from a prompt built from the state now."""
    prompt = world.prompt()
    if of is not None:
        body: Value = PlannerCorrection(of=of, planner=prompt, problem=ask.problem)
    elif ask.problem is not None:
        body = PlannerReask(planner=prompt, problem=ask.problem)
    else:
        body = prompt
    request = AskAgent(
        decision_id=DecisionId(root=f"planner.{ask.asks}"),
        scope=THIS_RUN,
        agent=Role.PLANNER.value,
        input=body,
    )
    wait = Wait(request=request, until=Within(limit=world.config.planner_turn_seconds))
    return ask.model_copy(update={"asks": ask.asks + 1}), wait


def begin(world: PlanWorld, problem: PlanProblem | None) -> tuple[Asking, list[Decision]]:
    """Open a call for the free slots, carrying why the previous call gave up, if it did."""
    ask, decision = _ask(world, Ask(capacity=world.capacity, problem=problem), of=None)
    return Asking(ask=ask), [decision]


def update(
    planning: Planning, event: Event, world: PlanWorld, view: RunView
) -> tuple[Planning, list[Decision]]:
    """One event of a planning call."""
    config = world.config
    match planning, event:
        case Asking(ask=ask), TurnFinished(reply=PortfolioPlan() as plan, invocation=turn):
            return _judge(ask, world, plan, None, turn)
        case Asking(ask=ask), Failed(reason=FailureReason.INVALID_REPLY, errors=errors, turn=turn):
            unparsed = PlanProblem(kind=ProblemKind.UNPARSEABLE, errors=errors)
            return _judge(ask, world, None, unparsed, turn)
        case Asking(ask=ask), Failed(
            reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED, turn=turn
        ):
            return _judge(ask, world, None, _no_reply(event), turn)
        case Asking(ask=ask), Failed(reason=FailureReason.LOST, turn=lost):
            if lost is None or ask.drops >= config.max_turn_drops:
                return _gave_up(world, _no_reply(event))
            until = view.now_at + config.backoff.delay(ask.drops)
            waiting = BackingOff(
                ask=ask.model_copy(update={"drops": ask.drops + 1}), until=until, lost=lost
            )
            return waiting, [WakeAt(time=until)]
        case Asking(), Failed():
            # refused, not rendered, or out of budget: the call ends and drops any held plan
            return _gave_up(world, _no_reply(event))
        case BackingOff(ask=ask, until=until, lost=lost), TimeReached():
            if view.now_at < until:
                return planning, []  # the wake-up of an earlier backoff
            asked, decision = _ask(world, ask, of=lost)
            return Asking(ask=asked), [decision]
        case _:
            raise UnexpectedEvent(planning, event)


def _no_reply(event: Failed) -> PlanProblem:
    return PlanProblem(
        kind=ProblemKind.NO_REPLY, ended=event.reason, turn_failure=event.turn_failure
    )


def _gave_up(world: PlanWorld, problem: PlanProblem | None) -> tuple[GaveUp, list[Decision]]:
    return GaveUp(problem=problem, stuck=world.active == 0, blocked_at_done=world.done), []


def _check(plan: PortfolioPlan, world: PlanWorld) -> PlanCheck:
    return validate(
        plan,
        world.ledger,
        world.offered,
        capacity=world.capacity,
        offer=world.config.profile_offer,
    )


def _problem(check: PlanCheck | None, found: PlanProblem | None, wanted: int) -> PlanProblem:
    """What was wrong with the reply, as the correction names it."""
    if found is not None:
        return found
    if check is None or check.valid:
        scheduled = 0 if check is None else len(check.accepted)
        return PlanProblem(kind=ProblemKind.UNDERFILLED, scheduled=scheduled, wanted=wanted)
    return PlanProblem(kind=ProblemKind.VIOLATIONS, violations=check.violations)


def _judge(
    ask: Ask,
    world: PlanWorld,
    plan: PortfolioPlan | None,
    found: PlanProblem | None,
    turn: InvocationRef | None,
) -> tuple[Planning, list[Decision]]:
    """Judge a reply, or the lack of one: schedule it, correct it, keep the best or give up."""
    check = None if plan is None else _check(plan, world)
    # The planner was asked to fill the slots free when the call began. A workstream that
    # finished while it was answering frees another slot, and the next call fills that one;
    # judging the reply against the later capacity would spend a correction on a slot the
    # planner was never offered.
    free = world.capacity
    want = min(ask.capacity, free) if ask.capacity else free
    if check is not None and check.valid and len(check.accepted) >= want:
        return Scheduled(check=check), []
    problem = _problem(check, found, want)
    if turn is not None and ask.corrections < world.config.max_corrections:
        held = plan if check is not None and check.valid and check.accepted else None
        again = ask.model_copy(
            update={
                "corrections": ask.corrections + 1,
                "drops": 0,
                "held": held or ask.held,
                "problem": problem,
            }
        )
        asked, decision = _ask(world, again, of=turn)
        return Asking(ask=asked), [decision]
    chosen = _final_choice(ask, world, check)
    if chosen is not None:
        return Scheduled(check=chosen), []
    if world.active == 0 and ask.fresh < world.config.max_retries_per_round:
        fresh = Ask(
            capacity=ask.capacity,
            asks=ask.asks,
            fresh=ask.fresh + 1,
            problem=problem,
        )
        asked, decision = _ask(world, fresh, of=None)
        return Asking(ask=asked), [decision]
    return _gave_up(world, problem)


def _final_choice(ask: Ask, world: PlanWorld, check: PlanCheck | None) -> PlanCheck | None:
    """The best valid outcome once corrections are exhausted."""
    if check is not None and check.valid and check.accepted:
        return check
    if ask.held is not None:
        kept = _check(ask.held, world)
        if kept.valid and kept.accepted:
            return kept
    if check is not None and check.accepted:
        return check
    return None
