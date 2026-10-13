"""The work of a workstream in its open attempt: implement and measure, or profile."""

from vibesys.candidates.api import stages
from vibesys.hypothesis import HypothesisOutcome
from vibesys.orchestration.dynamic.flow._ask import ask
from vibesys.orchestration.dynamic.flow._implement import implement_until_measurable
from vibesys.orchestration.dynamic.flow._measure import measure_and_interpret
from vibesys.orchestration.dynamic.flow._outcomes import (
    Attempt,
    Closing,
    WorkEndedError,
    WorkstreamStart,
    ended_by,
)
from vibesys.orchestration.dynamic.models import ImplementerResult
from vibesys.orchestration.dynamic.search.api import (
    Failure,
    FailureKind,
    Role,
    Unreachable,
    UnreachableReason,
    WorkKind,
    profile_prompt,
)
from vs_core.api.search import Failed, Flow, RunView


async def do_work(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Closing:
    """Let the role this workstream is for do its work in the attempt."""
    if w.plan.kind is WorkKind.PROFILE:
        return await profile(ctx, w, attempt)
    return await implement_and_measure(ctx, w, attempt)


async def implement_and_measure(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Closing:
    """Have the implementer make a candidate, then measure it with the trusted evaluations."""
    result = await implement_until_measurable(ctx, w, attempt)
    if result.candidate.outcome is HypothesisOutcome.BLOCKED or not stages(w.config):
        return Closing(result=result)  # nothing was made, or nothing is configured to measure
    evidence = await measure_and_interpret(ctx, w, attempt, result.candidate.revision or w.parent)
    return Closing(result=result, evidence=evidence)


async def profile(ctx: Flow, w: WorkstreamStart, attempt: Attempt) -> Closing:
    """Have the profiler look at the parent, then capture a trusted profile of it.

    What the profiler reports is never read; only what the trusted capture measures is.
    """
    config = w.config
    prompt = profile_prompt(w.plan, w.parent)
    seconds = config.profiler_turn_seconds
    event = await ask(ctx, attempt, Role.PROFILER, prompt, w.parent, seconds, ImplementerResult)
    if isinstance(event, Failed):
        raise ended_by(event, Role.PROFILER, plan=w.plan)
    if not config.profile_measurement:
        return Closing()
    if not ctx.read(_can_capture_profiles, bool):
        missing = Unreachable(
            reason=UnreachableReason.PROFILE_CAPABILITY_NOT_OFFERED, subject=w.plan.work_id
        )
        raise WorkEndedError(
            Failure(kind=FailureKind.PROFILE_CAPTURE_UNAVAILABLE), unreachable=missing
        )
    return Closing(evidence=await measure_and_interpret(ctx, w, attempt, w.parent))


def _can_capture_profiles(view: RunView) -> bool:
    return "profile-capture" in view.capabilities.lifecycle
