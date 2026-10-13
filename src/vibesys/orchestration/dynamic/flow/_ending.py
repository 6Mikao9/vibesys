"""How the search ends: choose the winner among the finished rounds, adopt it, finish the run."""

from vibesys.candidates import api as candidates
from vibesys.hypothesis import RunEnding
from vibesys.orchestration.dynamic.flow._run_state import RunRecord
from vs_core.api.search import (
    THIS_RUN,
    DecisionId,
    FailureReason,
    Finish,
    Flow,
    RunResultProposal,
)


async def finish(ctx: Flow, run: RunRecord) -> None:
    """Choose the winner, have it adopted, and end the run with the result."""
    winner = await select_and_adopt(ctx, run)
    ctx.publish(run.snapshot(winner))
    ending = candidates.finish(
        winner, stopping=ctx.stop_requested, no_winner_reason=no_winner_reason(run)
    )
    await ctx.finish(ending)
    # Core refused the result. A run that nobody ends would hang, so the plain failure is
    # proposed once.
    reason = candidates.ending_reason(RunEnding.NO_TRUSTED_RESULT, FailureReason.REFUSED.value)
    plain = RunResultProposal(outcome="failure", reason=reason)
    await ctx.finish(
        Finish(decision_id=DecisionId(root="finish"), scope=THIS_RUN, mode="drain", result=plain)
    )


async def select_and_adopt(ctx: Flow, run: RunRecord) -> candidates.Winner | None:
    """The winner core adopted, or None when there is nothing to adopt."""
    rounds = eligible_rounds(run)
    if (run.gate.failed and not rounds) or (not rounds and not candidates.trusted(run.baseline)):
        return None
    winner = candidates.choose_winner(rounds, run.config, ctx.facts.baseline)
    return await candidates.adopt(ctx, winner)


def eligible_rounds(run: RunRecord) -> tuple[candidates.EligibleRound, ...]:
    """Every winner-eligible round of the run."""
    return tuple(
        candidates.EligibleRound(
            hypothesis_id=hypothesis.hypothesis_id,
            sequence=row.sequence,
            settlement=row.settlement,
            candidate=row.candidate,
            metrics=row.metrics,
        )
        for hypothesis in run.hypotheses.values()
        for row in hypothesis.rounds
        if row.eligible and row.candidate is not None and row.settlement is not None
    )


def no_winner_reason(run: RunRecord) -> str:
    """Why the run ends without a winner, for the result it proposes."""
    problem = run.gate.last_error
    asked = problem.kind.value if problem is not None else ""
    if candidates.trusted(run.baseline) or eligible_rounds(run):
        return asked or "no candidate could be adopted"
    why = candidates.failure(run.baseline)
    detail = why.kind.value if why is not None else "the input did not pass its measurement"
    if asked:
        detail = f"{detail}; the planner's last reply: {asked}"
    return candidates.ending_reason(RunEnding.NO_TRUSTED_RESULT, detail)
