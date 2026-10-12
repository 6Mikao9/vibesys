"""One planning call: ask for a portfolio, correct it, and schedule what is valid.

The planner is a run-scoped, free agent. Its reply arrives already parsed against the reply
model; `plan` validates it against what the run has recorded, asks for a correction up to
`max_corrections` times, and when the corrections do not fix the plan keeps the best valid
outcome: a held underfilled plan, else the valid part of the last reply, else a fresh question,
else a typed give-up. A lost turn is asked again after a backoff on the run clock.

The agent's own retries are off (`Retry.none()`): every ask carries a prompt built from the run
at that moment, and every correction names the problem as typed data.
"""

from dataclasses import dataclass

from vibesys.orchestration.dynamic.flow._run_state import GaveUp, PlanOutcome, RunRecord, Scheduled
from vibesys.orchestration.dynamic.models import PortfolioPlan, planner_reply_type
from vibesys.orchestration.dynamic.search.api import (
    PlanCheck,
    PlannerCorrection,
    PlannerReask,
    PlanProblem,
    PlanWorld,
    ProblemKind,
    Role,
    validate,
)
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
    DecisionId,
    Failed,
    FailureReason,
    Flow,
    InvocationRef,
    RunView,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Within,
)


@dataclass
class Call:
    """What one planning call has spent and kept, which its asks and judgments read."""

    capacity: int
    """The slots that were free when the call began; a reply should fill them."""
    problem: PlanProblem | None
    """Why the last reply was not taken; the next prompt shows it."""
    corrections: int = 0
    drops: int = 0
    fresh: int = 0
    held: PlanCheck | None = None
    """The first valid plan that left slots free, kept in case a correction does not improve it."""


@dataclass(frozen=True)
class Reply:
    """A planner turn that ended: the plan it gave, or the problem with having none."""

    plan: PlanCheck | None
    problem: PlanProblem | None
    turn: InvocationRef | None


@dataclass(frozen=True)
class Again:
    """The call goes on with another ask: a correction of ``turn``, or a fresh question."""

    turn: InvocationRef | None


async def plan(ctx: Flow, run: RunRecord) -> PlanOutcome:
    """Ask the planner for a portfolio for the free slots, until one can be scheduled."""
    config = run.config
    call = Call(capacity=run.capacity(), problem=run.gate.last_error)
    again: InvocationRef | None = None
    """The turn the next ask corrects (or repeats, when it was lost); None for a fresh question."""
    while True:
        event = await ctx.wait(
            _ask(run.world(ctx), call, again),
            Within(limit=config.planner_turn_seconds),
            reply=planner_reply_type(config.profile_offer),
        )
        world = run.world(ctx)  # the run as it is when the reply arrives
        match event:
            case TurnFinished(reply=PortfolioPlan() as given, invocation=turn):
                reply = Reply(_check(given, world), None, turn)
            case Failed(reason=FailureReason.INVALID_REPLY, errors=errors, turn=turn):
                unparsed = PlanProblem(kind=ProblemKind.UNPARSEABLE, errors=errors)
                reply = Reply(None, unparsed, turn)
            case Failed(reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED, turn=turn):
                reply = Reply(None, _no_reply(event), turn)
            case Failed(reason=FailureReason.LOST, turn=lost) if (
                lost is not None and call.drops < config.max_turn_drops
            ):
                await _back_off(ctx, run, call)
                again = lost
                continue
            case Failed():
                return _gave_up(world, _no_reply(event))
            case unexpected:
                raise UnexpectedEvent(run, unexpected)
        verdict = _judge(call, world, reply)
        if isinstance(verdict, Again):
            again = verdict.turn
        else:
            return verdict


def _ask(world: PlanWorld, call: Call, again: InvocationRef | None) -> AskAgent:
    """The next question of the call, from a prompt built from the run now."""
    prompt = world.prompt()
    if again is not None:
        body: Value = PlannerCorrection(of=again, planner=prompt, problem=call.problem)
    elif call.problem is not None:
        body = PlannerReask(planner=prompt, problem=call.problem)
    else:
        body = prompt
    return AskAgent(
        decision_id=DecisionId(root="planner"),
        scope=THIS_RUN,
        agent=Role.PLANNER.value,
        input=body,
    )


async def _back_off(ctx: Flow, run: RunRecord, call: Call) -> None:
    """Wait out the backoff of a lost turn on the run clock before it is asked again."""
    until = ctx.read(_now, float) + run.config.backoff.delay(call.drops)
    call.drops += 1
    await ctx.wake_at(until)


def _judge(call: Call, world: PlanWorld, reply: Reply) -> PlanOutcome | Again:
    """Judge a reply, or the lack of one: schedule it, correct it, keep the best or give up."""
    config = world.config
    check = reply.plan
    # The planner was asked to fill the slots free when the call began. A workstream that
    # finished while it was answering frees another slot, and the next call fills that one;
    # judging the reply against the later capacity would spend a correction on a slot the
    # planner was never offered.
    want = min(call.capacity, world.capacity) if call.capacity else world.capacity
    if check is not None and check.valid and len(check.accepted) >= want:
        return Scheduled(check)
    call.problem = _problem(check, reply.problem, want)
    if reply.turn is not None and call.corrections < config.max_corrections:
        call.corrections += 1
        call.drops = 0
        call.held = _held(check) or call.held
        return Again(reply.turn)
    chosen = _final_choice(call, world, check)
    if chosen is not None:
        return Scheduled(chosen)
    if world.active == 0 and call.fresh < config.max_retries_per_round:
        call.fresh += 1
        call.held = None
        call.corrections = 0
        call.drops = 0
        return Again(None)
    return _gave_up(world, call.problem)


def _check(given: PortfolioPlan, world: PlanWorld) -> PlanCheck:
    return validate(
        given,
        world.ledger,
        world.offered,
        capacity=world.capacity,
        offer=world.config.profile_offer,
    )


def _held(check: PlanCheck | None) -> PlanCheck | None:
    return check if check is not None and check.valid and check.accepted else None


def _no_reply(event: Failed) -> PlanProblem:
    return PlanProblem(
        kind=ProblemKind.NO_REPLY, ended=event.reason, turn_failure=event.turn_failure
    )


def _gave_up(world: PlanWorld, problem: PlanProblem | None) -> GaveUp:
    return GaveUp(problem=problem, stuck=world.active == 0, blocked_at_done=world.done)


def _problem(check: PlanCheck | None, found: PlanProblem | None, wanted: int) -> PlanProblem:
    """What was wrong with the reply, as the correction names it."""
    if found is not None:
        return found
    if check is None or check.valid:
        scheduled = 0 if check is None else len(check.accepted)
        return PlanProblem(kind=ProblemKind.UNDERFILLED, scheduled=scheduled, wanted=wanted)
    return PlanProblem(kind=ProblemKind.VIOLATIONS, violations=check.violations)


def _final_choice(call: Call, world: PlanWorld, check: PlanCheck | None) -> PlanCheck | None:
    """The best valid outcome once corrections are exhausted."""
    if check is not None and check.valid and check.accepted:
        return check
    if call.held is not None and _schedulable(call.held, world):
        return call.held
    if check is not None and check.accepted:
        return check
    return None


def _schedulable(held: PlanCheck, world: PlanWorld) -> bool:
    """Whether a plan that was valid when it was held still fits what the run has now.

    The run may have moved while the corrections ran: a slot may have filled, an ID may have
    been taken, a parent may no longer be offered.
    """
    ledger = world.ledger
    taken = {item.hypothesis_id for item in ledger.hypotheses} | ledger.running | ledger.profile_ids
    offered = {
        (item.snapshot.hypothesis_id, item.snapshot.revision.revision_id.root)
        for item in world.offered
    }
    fresh = all(item.continue_hypothesis or item.work_id not in taken for item in held.accepted)
    parents = all(
        item.parent_hypothesis_id is None
        or item.parent_revision is None
        or (item.parent_hypothesis_id, item.parent_revision) in offered
        for item in held.accepted
    )
    return len(held.accepted) <= world.capacity and fresh and parents


def _now(view: RunView) -> float:
    return view.now_at
