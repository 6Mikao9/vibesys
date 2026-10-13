"""The run-scoped steps that design a hypothesis, shared by the serial searches.

A new hypothesis starts before any candidate does. In the profile-guided preset the configured
attribution command runs first, in a throwaway workspace holding the revision, and its ranked
components steer the designer. The designer then writes a plan: the ledger may refuse it, in
which case it is asked once more with the reason. What a search does between these two (multi
routes and profiles) is its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.candidates.api import space_of
from vibesys.hypothesis import HypothesisConfig, OrchestratorPlan, rounds
from vibesys.serial_search._attribution import (
    OUTPUT_ARGUMENT,
    AttributionConfig,
    attributed,
    focus_of,
    focus_policy,
)
from vibesys.serial_search._ledger import LedgerJson
from vibesys.serial_search._plans import accept_plan, base_commit, revision_of
from vibesys.serial_search._prompts import PlanCorrection, PlanPrompt, correction_of, plan_prompt
from vibesys.serial_search._roles import ORCHESTRATOR
from vs_core.api.search import (
    CaptureSpec,
    CommandCapture,
    Failed,
    Flow,
    RevisionRef,
    Session,
    TurnFinished,
    Value,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from vibesys.metrics import MetricSpace
    from vibesys.serial_search._plan_view import LastProfile
    from vibesys.serial_search._run import RoundStart

PLAN_CORRECTIONS = 1
"""Times the designer is asked to fix a plan the ledger refused, beyond core's own corrections."""


class AcceptedPlan(Value):
    """The designer's plan, accepted by the ledger."""

    plan: OrchestratorPlan


class Designed(Value):
    """A hypothesis designed for the round: the ledger that started it, its plan and its base."""

    ledger: LedgerJson
    plan: OrchestratorPlan
    base: RevisionRef


class NoDesign(Value):
    """The design ended without a plan; the search ends with this reason."""

    reason: str


async def capture_attribution(  # noqa: PLR0913  # LW-641202 [PLR0913]; the capture needs the preset, the revision, the ledger it updates and the round it is for.
    ctx: Flow,
    attribution: AttributionConfig,
    *,
    base: RevisionRef,
    ledger: LedgerJson,
    number: int,
    space: MetricSpace,
) -> LedgerJson | NoDesign:
    """Run the attribution command at ``base`` and fold its ranked components into the ledger."""
    spec = CaptureSpec(
        revision=base,
        argv=attribution.command,
        output_argument=OUTPUT_ARGUMENT,
        timeout_seconds=attribution.timeout_seconds,
    )
    within = float(attribution.timeout_seconds) + ctx.task.terms.operation_seconds
    match await ctx.evaluation.capture(spec, within=within):
        case Failed() as failed:
            reason = f"profile-guided attribution failed: the command ended ({failed.reason.value})"
            return NoDesign(reason=reason)
        case None:
            return NoDesign(reason="profile-guided attribution failed: no result")
        case CommandCapture() as capture:
            folded = attributed(ledger.decode(space), attribution, capture, round_number=number)
            return NoDesign(reason=folded) if isinstance(folded, str) else LedgerJson.of(folded)


async def ask_plan(  # LW-641203 [PLR0913]; the ask needs the conversation, the request, the ledger the plan is checked against and the round.
    planner: Session,
    prompt: PlanPrompt,
    *,
    hypotheses: HypothesisConfig,
    ledger: rounds.Ledger,
    number: int,
) -> AcceptedPlan | NoDesign:
    """Ask the designer for a plan; when the ledger refuses it, ask again with the reason."""
    asked: PlanPrompt | PlanCorrection = prompt
    problem = "the designer did not answer"
    for _ in range(1 + PLAN_CORRECTIONS):
        match await planner.turn(asked):
            case TurnFinished(reply=OrchestratorPlan() as reply, invocation=turn):
                accepted = accept_plan(hypotheses, ledger, reply, round_number=number)
                if isinstance(accepted, OrchestratorPlan):
                    return AcceptedPlan(plan=accepted)
                problem = accepted
                asked = correction_of(prompt, turn, problem)
            case Failed() as failed:
                return NoDesign(
                    reason=f"the designer's turn ended without a plan ({failed.reason.value})"
                )
            case TurnFinished() as unexpected:
                message = f"the designer answered with {type(unexpected.reply).__name__}"
                raise TypeError(message)
    return NoDesign(reason=problem)


type Advise = Callable[[rounds.Ledger], Awaitable[LastProfile | None]]
"""What a search does between attribution and the plan; the profile it learned, if any."""


async def design(  # LW-641204 [PLR0913]; the design needs the round, the preset and the cadence the ledger is read under.
    ctx: Flow,
    start: RoundStart,
    *,
    hypotheses: HypothesisConfig,
    attribution: AttributionConfig | None,
    advise: Advise | None = None,
) -> Designed | NoDesign:
    """Design a new hypothesis for the round: attribution, the search's own advice, the plan.

    The ledger returned has the hypothesis started in it; its base is the revision the ledger
    chose to build on (the newest checkpoint, or a frontier parent the plan reverts to).
    """
    terms = ctx.task.terms
    space = space_of(terms)
    ledger = start.ledger.decode(space)
    if attribution is not None:
        folded = await capture_attribution(
            ctx, attribution, base=start.base, ledger=start.ledger, number=start.number, space=space
        )
        if isinstance(folded, NoDesign):
            return folded
        ledger = folded.decode(space)
    profile = start.last_profile if advise is None else (await advise(ledger)) or start.last_profile
    prompt = plan_prompt(
        hypotheses,
        ledger,
        rounds.planning(hypotheses, ledger, start.number),
        focus_of(ledger, focus_policy(attribution)),
        base=start.base,
        benchmark_enabled="benchmark" in terms.measured,
        last_profile=profile,
    )
    asked = await ask_plan(
        ctx.agents.session(ORCHESTRATOR),
        prompt,
        hypotheses=hypotheses,
        ledger=ledger,
        number=start.number,
    )
    if isinstance(asked, NoDesign):
        return asked
    started_ledger, started = rounds.start(
        hypotheses,
        ledger,
        asked.plan,
        round_number=start.number,
        current_commit=start.base.revision_id.root,
        input_baseline=ctx.facts.baseline.revision_id.root,
    )
    base = revision_of(base_commit(started), ctx.facts.baseline)
    return Designed(ledger=LedgerJson.of(started_ledger), plan=asked.plan, base=base)
