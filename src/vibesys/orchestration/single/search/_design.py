"""The design of a new hypothesis: attribution, then a plan the ledger accepts.

A new hypothesis starts with the designer, a run-scoped free agent that may write the roadmap
artifacts. In the profile-guided preset the configured attribution command runs first, in a
throwaway candidate, and its ranked components steer the designer. The designer's reply is
checked against the ledger and corrected (`vibesys.serial_search`); a valid plan starts the
hypothesis in the ledger and hands the search the round it opens. A hypothesis the ledger keeps
active continues without a design.

Design is a child machine of the search. It carries the hypothesis ledger it was started with and
the focus the attribution adds; the search takes it back when the design ends.
"""

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from vibesys.hypothesis import OrchestratorPlan, rounds
from vibesys.orchestration.single.search._config import SingleConfig
from vibesys.orchestration.single.search._operations import CaptureCommand
from vibesys.orchestration.single.search._prompts import PlanPrompt
from vibesys.orchestration.single.search._round import RoundStart
from vibesys.serial_search.api import (
    OUTPUT_ARGUMENT,
    Asking,
    AttributionFailure,
    AttributionKind,
    GaveUp,
    GiveUp,
    LastProfile,
    PlanEvidence,
    Planned,
    PlanWorld,
    Role,
    StoredLedger,
    base_commit,
    begin_planning,
    decode_ledger,
    encode_ledger,
    focus_of,
    focus_policy,
    fold_capture,
    plan_evidence,
    update_planning,
)
from vs_core.api.search import (
    THIS_RUN,
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


class Capturing(Value):
    """The attribution command runs; its output steers the designer."""

    kind: Literal["capturing"] = "capturing"


class Designed(Value):
    """The design ended with a plan the ledger started: the round to open."""

    kind: Literal["designed"] = "designed"
    start: RoundStart


class Abandoned(Value):
    """The design ended with no plan; no further round can start."""

    kind: Literal["abandoned"] = "abandoned"
    giveup: GiveUp


type DesignPhase = Annotated[Capturing | Asking | Designed | Abandoned, Field(discriminator="kind")]


class Design(Value):
    """One design: the round it is for, the revision it builds on and the phase it is in."""

    kind: Literal["design"] = "design"
    number: int = Field(gt=0)
    base: RevisionRef
    ledger: StoredLedger
    phase: DesignPhase


@dataclass(frozen=True)
class Env:
    """What a design reads and never keeps: the policy, the input revision and the last profile."""

    config: SingleConfig
    baseline: RevisionRef
    last_profile: LastProfile | None


def _world(design: Design, env: Env) -> PlanWorld:
    config = env.config
    return PlanWorld(
        config=config.hypotheses,
        ledger=decode_ledger(design.ledger, config.metric_space),
        round_number=design.number,
        agent=Role.PLANNER.value,
        seconds=config.planner_turn_seconds,
        max_corrections=config.max_corrections,
    )


def _prompt(design: Design, env: Env) -> PlanPrompt:
    """The new-hypothesis request: history, the frontier, carried notices and the last profile."""
    config = env.config
    ledger = decode_ledger(design.ledger, config.metric_space)
    planning = rounds.planning(config.hypotheses, ledger, design.number)
    focus = focus_of(ledger, focus_policy(config.attribution))
    evidence = plan_evidence(config.hypotheses, ledger, planning, focus).model_copy(
        update={
            "benchmark_enabled": config.benchmark_configured,
            "last_profile": env.last_profile,
        }
    )
    return PlanPrompt(
        **{name: getattr(evidence, name) for name in PlanEvidence.model_fields},
        base_revision=design.base,
    )


def begin(
    number: int, base: RevisionRef, ledger: StoredLedger, env: Env
) -> tuple[Design, list[Decision]]:
    """Run the attribution when the preset needs it, else ask the designer at once."""
    attribution = env.config.attribution
    design = Design(number=number, base=base, ledger=ledger, phase=Capturing())
    if attribution is None:
        return _planning(design, env)
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
    return design, [Wait(request=request, until=Within(limit=env.config.operation_seconds))]


def _planning(design: Design, env: Env) -> tuple[Design, list[Decision]]:
    asking, decisions = begin_planning(_world(design, env), _prompt(design, env))
    return design.model_copy(update={"phase": asking}), decisions


def update(design: Design, event: Event, env: Env) -> tuple[Design, list[Decision]]:
    """One event of a design."""
    match design.phase, event:
        # an operator stop never cancels the design; the search drops its plan when it ends
        case Capturing() | Asking(), StopRequested():
            return design, []
        case Capturing(), OperationFinished():
            return _captured(design, env, event)
        case Capturing(), Failed():
            failure = AttributionFailure(kind=AttributionKind.ENDED, ended=event.reason)
            return _abandoned(design, GiveUp(attribution=failure)), []
        case Asking(), TurnFinished() | Failed():
            return _planned(design, event, env)
        case _:
            raise UnexpectedEvent(design.phase, event)


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
    return _planning(design.model_copy(update={"ledger": encode_ledger(folded)}), env)


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
    )
    return design.model_copy(update={"phase": Designed(start=start)})
