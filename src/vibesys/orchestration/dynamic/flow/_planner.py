"""One planning call: ask for a portfolio, correct it, and schedule what is valid.

The planner is a run-scoped, free agent. Its reply arrives already parsed against the reply
model; `plan` validates it against what the run has recorded, asks for a correction up to
`max_corrections` times, and when the corrections do not fix the plan keeps the best valid
outcome: a held underfilled plan, else the valid part of the last reply, else a fresh question,
else a typed give-up. A lost turn is asked again, a few times, after a backoff on the run clock.

The agent's own retries are off (`Retry.none()`): every ask carries a prompt built from the run
at that moment, and every correction names the problem as typed data.
"""

from dataclasses import dataclass

from vibesys.orchestration.dynamic.flow import _parents
from vibesys.orchestration.dynamic.flow._run_state import NoPlan, PlanOutcome, RunRecord, Scheduled
from vibesys.orchestration.dynamic.models import PortfolioPlan, planner_reply_type
from vibesys.orchestration.dynamic.search.api import (
    DynamicConfig,
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
    Again,
    AskAgent,
    DecisionId,
    Failed,
    FailureReason,
    Flow,
    GaveUp,
    InvocationRef,
    RetryPolicy,
    Tries,
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
    again: InvocationRef | None = None
    """The turn the next ask corrects; None for a fresh question."""
    corrections: int = 0
    fresh: int = 0
    held: PlanCheck | None = None
    """The first valid plan that left slots free, kept in case a correction does not improve it."""


@dataclass(frozen=True)
class Reply:
    """A planner turn that ended, judged against the run as it was when it arrived."""

    plan: PlanCheck | None
    problem: PlanProblem | None
    turn: InvocationRef | None
    world: PlanWorld


def lost_turns(config: DynamicConfig) -> RetryPolicy:
    """A turn the agent lost is asked again, up to `max_turn_drops` times, after a backoff."""
    return RetryPolicy(max_tries=config.max_turn_drops + 1, backoff=config.backoff)


async def plan(ctx: Flow, run: RunRecord) -> PlanOutcome:
    """Ask the planner for a portfolio for the free slots, until one can be scheduled."""
    call = Call(capacity=run.capacity(), problem=run.gate.last_error)

    async def ask(tries: Tries[Failed]) -> Reply | NoPlan | Again[Failed]:
        return await ask_planner(ctx, run, call, tries.reasons)

    while True:
        match await ctx.retry(ask, lost_turns(run.config)):
            case GaveUp(reasons=lost):
                return _no_plan(_world(ctx, run), _no_reply(lost[-1]))
            case NoPlan() as outcome:
                return outcome
            case Reply() as reply:
                if (outcome := _judge(call, reply)) is not None:
                    return outcome


async def ask_planner(
    ctx: Flow, run: RunRecord, call: Call, lost: tuple[Failed, ...]
) -> Reply | NoPlan | Again[Failed]:
    """One ask: the planner's reply, a plan-less end, or a lost turn to ask again."""
    asked_of = lost[-1].turn if lost else call.again
    event = await ctx.wait(
        _ask(_world(ctx, run), call, asked_of),
        Within(limit=run.config.planner_turn_seconds),
        reply=planner_reply_type(run.config.profile_offer),
    )
    world = _world(ctx, run)  # the run as it is when the reply arrives
    match event:
        case TurnFinished(reply=PortfolioPlan() as given, invocation=turn):
            return Reply(_check(given, world), None, turn, world)
        case Failed(reason=FailureReason.INVALID_REPLY, errors=errors, turn=turn):
            unparsed = PlanProblem(kind=ProblemKind.UNPARSEABLE, errors=errors)
            return Reply(None, unparsed, turn, world)
        case Failed(reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED, turn=turn):
            return Reply(None, _no_reply(event), turn, world)
        case Failed(reason=FailureReason.LOST, turn=lost_turn) if lost_turn is not None:
            return Again(reason=event)
        case Failed():
            return _no_plan(world, _no_reply(event))
        case unexpected:
            raise UnexpectedEvent(run, unexpected)


def _world(ctx: Flow, run: RunRecord) -> PlanWorld:
    return run.world(_parents.offered(ctx, run))


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


def _judge(call: Call, reply: Reply) -> PlanOutcome | None:
    """Judge a reply: schedule it, keep the best, give up, or set up the next ask (None)."""
    world = reply.world
    check = reply.plan
    # The planner was asked to fill the slots free when the call began. A workstream that
    # finished while it was answering frees another slot, and the next call fills that one;
    # judging the reply against the later capacity would spend a correction on a slot the
    # planner was never offered.
    want = min(call.capacity, world.capacity) if call.capacity else world.capacity
    if check is not None and check.valid and len(check.accepted) >= want:
        return Scheduled(check)
    call.problem = _problem(check, reply.problem, want)
    if reply.turn is not None and call.corrections < world.config.max_corrections:
        call.corrections += 1
        call.held = _held(check) or call.held
        call.again = reply.turn
        return None
    chosen = _final_choice(call, world, check)
    if chosen is not None:
        return Scheduled(chosen)
    if world.active == 0 and call.fresh < world.config.max_retries_per_round:
        call.fresh += 1
        call.held = None
        call.corrections = 0
        call.again = None
        return None
    return _no_plan(world, call.problem)


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


def _no_plan(world: PlanWorld, problem: PlanProblem | None) -> NoPlan:
    return NoPlan(problem=problem, stuck=world.active == 0, blocked_at_done=world.done)


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
