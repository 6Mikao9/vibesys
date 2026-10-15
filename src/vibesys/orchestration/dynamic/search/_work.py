"""The work of a workstream in its open workspace: implement and measure, or profile."""

from vibesys.hypothesis import HypothesisOutcome
from vibesys.orchestration.dynamic.search._briefs import profile_prompt
from vibesys.orchestration.dynamic.search._implement import (
    Conversation,
    implement_until_measurable,
)
from vibesys.orchestration.dynamic.search._measure import measure_and_interpret
from vibesys.orchestration.dynamic.search._outcomes import (
    Closing,
    Work,
    WorkEndedError,
    WorkstreamStart,
    ended_by,
)
from vibesys.orchestration.dynamic.search._roles import IMPLEMENTER, JUDGE, PROFILER
from vibesys.orchestration.dynamic.search._values import AgentName, WorkKind
from vs_core.api.search import Failed, Flow, Workspace


async def do_work(ctx: Flow, w: WorkstreamStart, ws: Workspace, work: Work) -> Closing:
    """Let the role this workstream is for do its work; a work that cannot go on closes as failed."""
    try:
        if w.plan.kind is WorkKind.PROFILE:
            return await profile(ctx, w, ws, work)
        return await implement_and_measure(ctx, w, ws, work)
    except WorkEndedError as ended:
        return work.ended(ended)


async def implement_and_measure(
    ctx: Flow, w: WorkstreamStart, ws: Workspace, work: Work
) -> Closing:
    """Have the implementer make a candidate, then measure it with the trusted evaluations."""
    sessions = Conversation(
        implementer=ctx.agents.session(IMPLEMENTER, workspace=ws, revision=w.parent),
        judge=ctx.agents.session(JUDGE, workspace=ws),
    )
    result = await implement_until_measurable(ctx, w, ws, work, sessions)
    if (
        result.candidate.outcome is HypothesisOutcome.BLOCKED
        or not ctx.task.terms.measured
        or not w.plan.measured
    ):
        # nothing was made, nothing is configured to measure, or a plan step is accepted on
        # the judge's review of its own checks
        return Closing(result=result)
    revision = result.candidate.revision or w.parent
    evidence = await measure_and_interpret(ctx, w, ws, work, revision)
    return Closing(result=result, evidence=evidence)


async def profile(ctx: Flow, w: WorkstreamStart, ws: Workspace, work: Work) -> Closing:
    """Have the profiler look at the parent, then capture a trusted profile of it.

    What the profiler reports is never read; only what the trusted capture measures is.
    """
    profiler = ctx.agents.session(PROFILER, workspace=ws, revision=w.parent)
    if isinstance(failed := await profiler.turn(profile_prompt(w.plan, w.parent)), Failed):
        raise ended_by(failed, AgentName.PROFILER, plan=w.plan)
    return Closing(evidence=await measure_and_interpret(ctx, w, ws, work, w.parent))
