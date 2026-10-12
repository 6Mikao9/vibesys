"""The design of a new hypothesis: attribution, routing, profile, then a plan the ledger accepts.

A new hypothesis starts with run-scoped, free turns before any attempt. In the profile-guided
preset the configured attribution command runs first, in a throwaway candidate, and its ranked
components steer the designer. When the run offers a profiler, a routing turn decides whether
fresh profile evidence is wanted and a profiler turn collects it. The designer then writes the
plan, checked against the ledger and corrected (`vibesys.serial_search`); a valid plan starts the
hypothesis in the ledger and hands the search the round it opens. The routing and profiling turns
are advisory: a turn that gives no answer is a note and the plan goes ahead. Attribution and the
plan are the run's contract, so a failure of either ends the search. A hypothesis the ledger keeps
active continues without a design.

Design is a child machine of the search. It carries the hypothesis ledger it was started with, the
focus the attribution adds and the notes it makes; the search takes it back when the design ends.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from vibesys.hypothesis import OrchestratorPlan, PlanningContext, rounds
from vibesys.orchestration.multi.models import PreRoundDecision
from vibesys.orchestration.multi.search import _briefs as briefs
from vibesys.orchestration.multi.search._config import MultiConfig
from vibesys.orchestration.multi.search._operations import CaptureCommand
from vibesys.orchestration.multi.search._round import RoundStart
from vibesys.orchestration.multi.search._values import Note, NoteKind
from vibesys.orchestration.profilers import ProfilerSummary
from vibesys.serial_search.api import (
    OUTPUT_ARGUMENT,
    Asking,
    AttributionFailure,
    AttributionKind,
    GaveUp,
    GiveUp,
    LastProfile,
    OfficialReason,
    PlanEvidence,
    Planned,
    PlanWorld,
    Role,
    StoredLedger,
    base_commit,
    begin_planning,
    decode_ledger,
    encode_ledger,
    failure_of,
    focus_of,
    focus_policy,
    fold_capture,
    official_reason,
    plan_evidence,
    update_planning,
)
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
    CommandCapture,
    Decision,
    DecisionId,
    Event,
    Failed,
    Operation,
    OperationFinished,
    RevisionRef,
    StopRequested,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Wait,
    Within,
)

DEFAULT_FOCUS = "general steady-state benchmark hotspots"
"""What the profiler looks at when the routing turn wants a profile and names no focus."""


class Capturing(Value):
    """The attribution command runs; its output steers the designer."""

    kind: Literal["capturing"] = "capturing"


class Routing(Value):
    """The routing turn is out: is fresh profile evidence wanted before the plan?"""

    kind: Literal["routing"] = "routing"


class Profiling(Value):
    """The profiler turn is out, looking at ``focus``."""

    kind: Literal["profiling"] = "profiling"
    focus: str


class Designed(Value):
    """The design ended with a plan the ledger started: the round to open."""

    kind: Literal["designed"] = "designed"
    start: RoundStart


class Abandoned(Value):
    """The design ended with no plan; no further round can start."""

    kind: Literal["abandoned"] = "abandoned"
    giveup: GiveUp


type DesignPhase = Annotated[
    Capturing | Routing | Profiling | Asking | Designed | Abandoned, Field(discriminator="kind")
]


class Design(Value):
    """One design: the round it is for, the revision it builds on and the phase it is in."""

    kind: Literal["design"] = "design"
    number: int = Field(gt=0)
    base: RevisionRef
    ledger: StoredLedger
    phase: DesignPhase
    profile: LastProfile | None = None
    """The fresh profile of this design, for the planning request."""
    notes: tuple[Note, ...] = ()


@dataclass(frozen=True)
class Env:
    """What a design reads and never keeps: the policy and the input revision."""

    config: MultiConfig
    baseline: RevisionRef


def planned_reason(
    config: MultiConfig, ledger: rounds.Ledger, plan: OrchestratorPlan, number: int
) -> OfficialReason | None:
    """Why the official gates are scheduled after this round's candidate, as the prompts show it."""
    reason = official_reason(
        config.hypotheses,
        ledger,
        round_number=number,
        requested=plan.request_official_evaluation,
    )
    focus = focus_of(ledger, focus_policy(config.attribution))
    if reason is None and focus is not None and focus.active_component:
        return OfficialReason.COMPONENT_MEASUREMENT
    return reason


def _world(design: Design, env: Env) -> PlanWorld:
    config = env.config
    return PlanWorld(
        config=config.hypotheses,
        ledger=decode_ledger(design.ledger, config.metric_space),
        round_number=design.number,
        agent=Role.PLANNER.value,
        seconds=config.orchestrator_turn_seconds,
        max_corrections=config.max_corrections,
    )


def _evidence(design: Design, env: Env) -> tuple[PlanEvidence, PlanningContext]:
    config = env.config
    ledger = decode_ledger(design.ledger, config.metric_space)
    planning = rounds.planning(config.hypotheses, ledger, design.number)
    focus = focus_of(ledger, focus_policy(config.attribution))
    return plan_evidence(config.hypotheses, ledger, planning, focus), planning


def _wait(request: AskAgent | Operation, seconds: float) -> Decision:
    return Wait(request=request, until=Within(limit=seconds))


def begin(
    number: int, base: RevisionRef, ledger: StoredLedger, env: Env
) -> tuple[Design, list[Decision]]:
    """Run the attribution when the preset needs it, else the next step of the design."""
    attribution = env.config.attribution
    design = Design(number=number, base=base, ledger=ledger, phase=Capturing())
    if attribution is None:
        return _routing(design, env)
    request = Operation(
        decision_id=DecisionId(root=f"capture.{number}"),
        scope=THIS_RUN,
        request=CaptureCommand(
            revision=base,
            argv=attribution.command,
            output_argument=OUTPUT_ARGUMENT,
            timeout_seconds=attribution.timeout_seconds,
        ),
    )
    return design, [_wait(request, env.config.operation_seconds)]


def _routing(design: Design, env: Env) -> tuple[Design, list[Decision]]:
    """Ask whether a profile is wanted, when the run offers a profiler; else plan."""
    if not env.config.profiling:
        return _planning(design, env)
    evidence, planning = _evidence(design, env)
    request = AskAgent(
        decision_id=DecisionId(root=f"preround.{design.number}"),
        scope=THIS_RUN,
        agent=Role.PREROUND.value,
        input=briefs.pre_round_prompt(evidence, env.config, planning),
    )
    moved = design.model_copy(update={"phase": Routing()})
    return moved, [_wait(request, env.config.orchestrator_turn_seconds)]


def _profiling(design: Design, env: Env, focus: str) -> tuple[Design, list[Decision]]:
    request = AskAgent(
        decision_id=DecisionId(root=f"profile.{design.number}"),
        scope=THIS_RUN,
        agent=Role.PROFILER.value,
        input=briefs.profiler_prompt(design.number, env.config, focus),
    )
    moved = design.model_copy(update={"phase": Profiling(focus=focus)})
    return moved, [_wait(request, env.config.profiler_turn_seconds)]


def _planning(design: Design, env: Env) -> tuple[Design, list[Decision]]:
    evidence, _ = _evidence(design, env)
    prompt = briefs.plan_prompt(evidence, env.config, design.profile, design.base)
    asking, decisions = begin_planning(_world(design, env), prompt)
    return design.model_copy(update={"phase": asking}), decisions


def update(design: Design, event: Event, env: Env) -> tuple[Design, list[Decision]]:
    """One event of a design."""
    match design.phase, event:
        # an operator stop never cancels the design; the search drops its plan when it ends
        case Capturing() | Routing() | Profiling() | Asking(), StopRequested():
            return design, []
        case Capturing(), OperationFinished():
            return _captured(design, env, event)
        case Capturing(), Failed():
            failure = AttributionFailure(kind=AttributionKind.ENDED, ended=event.reason)
            return _abandoned(design, GiveUp(attribution=failure)), []
        case Routing() | Profiling(), TurnFinished():
            return _advised(design, env, event)
        case Routing() | Profiling(), Failed():
            return _planning(_skipped(design, event), env)
        case Asking(), TurnFinished() | Failed():
            return _planned(design, event, env)
        case _:
            raise UnexpectedEvent(design.phase, event)


def _skipped(design: Design, event: Failed) -> Design:
    """An advisory turn gave no answer: note it and let the plan go ahead without it."""
    routing = isinstance(design.phase, Routing)
    note = Note(
        kind=NoteKind.PRE_ROUND_SKIPPED if routing else NoteKind.PROFILE_SKIPPED,
        failure=failure_of(event, Role.PREROUND if routing else Role.PROFILER),
    )
    return design.model_copy(update={"notes": (*design.notes, note)})


def _abandoned(design: Design, giveup: GiveUp) -> Design:
    return design.model_copy(update={"phase": Abandoned(giveup=giveup)})


def _captured(design: Design, env: Env, event: OperationFinished) -> tuple[Design, list[Decision]]:
    """Fold the attribution into the focus state once; a failed command ends the run."""
    attribution = env.config.attribution
    outcome = event.result.outcome
    if attribution is None or not isinstance(outcome, CommandCapture):
        raise UnexpectedEvent(design.phase, event)
    ledger = decode_ledger(design.ledger, env.config.metric_space)
    folded = fold_capture(ledger, attribution, outcome, round_number=design.number)
    if isinstance(folded, AttributionFailure):
        return _abandoned(design, GiveUp(attribution=folded)), []
    return _routing(design.model_copy(update={"ledger": encode_ledger(folded)}), env)


def _advised(design: Design, env: Env, event: TurnFinished) -> tuple[Design, list[Decision]]:
    """An advisory turn answered: route to a profile, or fold the profile into the design."""
    match design.phase, event.reply:
        case Routing(), PreRoundDecision() as decision:
            return _routed(design, env, decision)
        case Profiling() as profiling, ProfilerSummary() as summary:
            return _profiled(design, env, profiling, summary)
        case _:
            raise UnexpectedEvent(design.phase, event)


def _routed(design: Design, env: Env, decision: PreRoundDecision) -> tuple[Design, list[Decision]]:
    if decision.need_profile:
        return _profiling(design, env, decision.profile_focus or DEFAULT_FOCUS)
    return _planning(design, env)


def _profiled(
    design: Design, env: Env, phase: Profiling, summary: ProfilerSummary
) -> tuple[Design, list[Decision]]:
    profile = LastProfile(
        source="profiler",
        analysis=summary.analysis,
        bottlenecks=summary.bottlenecks,
        suggestions=summary.suggestions,
        perf_metric=summary.perf_metric,
        perf_unit=summary.perf_unit,
        evidence_directory=briefs.profiler_prompt(
            design.number, env.config, phase.focus
        ).evidence_directory,
    )
    return _planning(design.model_copy(update={"profile": profile}), env)


def _planned(design: Design, event: Event, env: Env) -> tuple[Design, list[Decision]]:
    if not isinstance(design.phase, Asking):
        raise UnexpectedEvent(design.phase, event)
    planning, decisions = update_planning(design.phase, event, _world(design, env))
    match planning:
        case Asking():
            return design.model_copy(update={"phase": planning}), decisions
        case GaveUp(problem=problem):
            return _abandoned(design, GiveUp(problem=problem)), []
        case Planned(plan=plan):
            return _started(design, env, plan), []


def _started(design: Design, env: Env, plan: OrchestratorPlan) -> Design:
    """Start the planned hypothesis in the ledger; the round it opens builds on its base."""
    config = env.config
    started_ledger, started = rounds.start(
        config.hypotheses,
        decode_ledger(design.ledger, config.metric_space),
        plan,
        round_number=design.number,
        current_commit=design.base.revision_id.root,
        input_baseline=env.baseline.revision_id.root,
    )
    commit = base_commit(started)
    start = RoundStart(
        number=design.number,
        hypothesis_id=plan.hypothesis_id,
        plan_json=plan.model_dump_json(),
        base=env.baseline if not commit else RevisionRef.of_git_commit(commit),
        ledger=encode_ledger(started_ledger),
        revalidation_required=started.hypothesis.gate_revalidation_pending,
        planned=planned_reason(config, started_ledger, plan, design.number),
    )
    return design.model_copy(update={"phase": Designed(start=start)})
