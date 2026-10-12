"""One planning call: ask the designer for a plan, correct it, and hand back what the ledger accepts.

The designer is a run-scoped, free agent. Its reply arrives already parsed; this machine checks
it against the ledger (`accept_plan`) and, when it cannot be taken, asks the same conversation to
fix it, up to `max_corrections` times, naming the problem as typed data (`PlanCorrection`). A turn
that ended without a reply is asked again the same way while its turn is known. A turn the
transport lost is core's to ask again (the agent's `Retry.lost_turns`), so a lost turn that
reaches this machine is an exhausted one.

The machine ends as `Planned` (a plan the ledger can start) or `GaveUp` (the last problem). It
never touches the ledger; starting the hypothesis is the caller's.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from vibesys.hypothesis import HypothesisConfig, OrchestratorPlan, rounds
from vibesys.serial_search._plans import (
    PlanCorrection,
    PlanProblem,
    ProblemKind,
    accept_plan,
)
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
    Decision,
    DecisionId,
    Event,
    Failed,
    FailureReason,
    InvocationRef,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Wait,
    Within,
)

PLAN_PREFIX = "plan"
"""Every ask of a planning call is named under it, so one conversation serves every round."""


@dataclass(frozen=True)
class PlanWorld:
    """What a planning call reads and never keeps."""

    config: HypothesisConfig
    ledger: rounds.Ledger
    round_number: int
    agent: str
    seconds: float
    max_corrections: int


class Asking(Value):
    """A designer turn is out."""

    kind: Literal["asking"] = "asking"
    asks: int = Field(ge=1)
    """Asks sent so far; the next ask is numbered by it."""
    corrections: int = Field(default=0, ge=0)


class Planned(Value):
    """The call ended with a plan the ledger can start."""

    kind: Literal["planned"] = "planned"
    plan: OrchestratorPlan


class GaveUp(Value):
    """The call ended with no plan; ``problem`` is what was wrong with the last turn."""

    kind: Literal["gave_up"] = "gave_up"
    problem: PlanProblem


type Planning = Annotated[Asking | Planned | GaveUp, Field(discriminator="kind")]


def _ask(world: PlanWorld, asks: int, body: Value) -> Decision:
    request = AskAgent(
        decision_id=DecisionId(root=f"{PLAN_PREFIX}/{world.round_number}.{asks}"),
        scope=THIS_RUN,
        agent=world.agent,
        input=body,
    )
    return Wait(request=request, until=Within(limit=world.seconds))


def begin(world: PlanWorld, prompt: Value) -> tuple[Asking, list[Decision]]:
    """Open a call with the designer's first question."""
    return Asking(asks=1), [_ask(world, 0, prompt)]


def update(planning: Planning, event: Event, world: PlanWorld) -> tuple[Planning, list[Decision]]:
    """One event of a planning call."""
    match planning, event:
        case Asking() as asking, TurnFinished(reply=OrchestratorPlan() as plan, invocation=turn):
            accepted = accept_plan(
                world.config, world.ledger, plan, round_number=world.round_number
            )
            if isinstance(accepted, PlanProblem):
                return _corrected(asking, world, accepted, turn)
            return Planned(plan=accepted), []
        case Asking() as asking, Failed(
            reason=FailureReason.INVALID_REPLY, errors=errors, turn=turn
        ):
            unparsed = PlanProblem(kind=ProblemKind.UNPARSEABLE, errors=errors)
            return _corrected(asking, world, unparsed, turn)
        case Asking() as asking, Failed(
            reason=FailureReason.TIMED_OUT | FailureReason.TURN_FAILED, turn=turn
        ):
            return _corrected(asking, world, _no_reply(event), turn)
        case Asking(), Failed():
            return GaveUp(problem=_no_reply(event)), []
        case _:
            raise UnexpectedEvent(planning, event)


def _no_reply(event: Failed) -> PlanProblem:
    return PlanProblem(
        kind=ProblemKind.NO_REPLY, ended=event.reason, turn_failure=event.turn_failure
    )


def _corrected(
    asking: Asking, world: PlanWorld, problem: PlanProblem, turn: InvocationRef | None
) -> tuple[Planning, list[Decision]]:
    """Ask the designer to fix the turn, or give up when no correction is left or possible."""
    if turn is None or asking.corrections >= world.max_corrections:
        return GaveUp(problem=problem), []
    again = Asking(asks=asking.asks + 1, corrections=asking.corrections + 1)
    return again, [_ask(world, asking.asks, PlanCorrection(of=turn, problem=problem))]
