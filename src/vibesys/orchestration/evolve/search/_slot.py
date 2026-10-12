"""One slot: a child of the search from its seat to its settlement.

A slot is one attempt. Its phase says what it waits for and holds only the data valid there; each
phase has one handler, a `match` over the event that ends in `UnexpectedEvent`. A seated slot
opens its workspace, asks the mutator (paid) for a candidate, has the judge review the exact
candidate (free), measures the gates the run configured, decodes the evidence, asks the advisory
profiler about a passing candidate, exports its patch for a selector that ranks by code, and
settles the workspace. A child does not retry its mutation: a failed child is a failed
individual, and only its feedback survives.

When the slot is over its phase is `Done`; the search admits it to the population in slot order
and drops the child.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import Field

from vibesys.candidates.api import stages, until_deadline
from vibesys.metrics import accept_readings, judged_evidence, reading_of
from vibesys.orchestration.evolve.models import JudgeResponse, MutatorResponse
from vibesys.orchestration.evolve.search import _context as context
from vibesys.orchestration.evolve.search._config import EvolveConfig
from vibesys.orchestration.evolve.search._operations import (
    EvidenceReadings,
    ExportPatch,
    InterpretEvidence,
    PatchExport,
)
from vibesys.orchestration.evolve.search._prompts import MutatePrompt, PromptContext
from vibesys.orchestration.evolve.search._recipe import measurement_recipe
from vibesys.orchestration.evolve.search._settlement import settle_for
from vibesys.orchestration.evolve.search._values import (
    Failure,
    FailureKind,
    Finding,
    ProfileReading,
    Role,
    SettledSlot,
    SlotPlan,
    Unreachable,
    UnreachableReason,
)
from vibesys.orchestration.profilers import ProfilerSummary
from vibesys.orchestration.review import Verdict
from vs_core.api.search import (
    THIS_RUN,
    AskAgent,
    AttemptBudget,
    AttemptId,
    AttemptRef,
    Decision,
    DecisionId,
    Declined,
    Event,
    EvidenceKind,
    EvidenceRef,
    Failed,
    FailureReason,
    Measure,
    MeasurementFinished,
    MeasurementResult,
    Operation,
    OperationFinished,
    RevisionRef,
    RunView,
    Scope,
    Settle,
    Settlement,
    StartAttempt,
    StopRequested,
    TurnFinished,
    UnexpectedEvent,
    Value,
    Wait,
    WaitRefusal,
    Withdraw,
    Within,
    WorkspaceClosed,
    WorkspaceOpened,
    WorkspacePlan,
    attempt_id,
    item_id,
    may_resubmit,
    trusted_keys,
)

START = DecisionId(root="start")
SETTLE = DecisionId(root="settle")
SETTLE_AGAIN = DecisionId(root="settle.1")
INTERPRET = DecisionId(root="interpret")
EXPORT = DecisionId(root="export")

NO_ANSWER = frozenset(
    {
        FailureReason.INVALID_REPLY,
        FailureReason.LOST,
        FailureReason.TURN_FAILED,
        FailureReason.TIMED_OUT,
    }
)
"""The ways a turn ends without an answer: the reasons that cost an individual only its feedback."""


# -- phases ------------------------------------------------------------------------------------------


class Queued(Value):
    """The slot is in its frame and waits for a seat."""

    kind: Literal["queued"] = "queued"


class Opening(Value):
    """The attempt was requested; ``stopping`` records a stop that arrived before it opened."""

    kind: Literal["opening"] = "opening"
    brief: MutatePrompt
    """What the mutator will be asked, fixed when the slot was seated."""
    stopping: bool = False


class Mutating(Value):
    """A mutator turn is out."""

    kind: Literal["mutating"] = "mutating"


class Reviewing(Value):
    """A judge turn is out on the mutator's candidate."""

    kind: Literal["reviewing"] = "reviewing"
    finding: Finding


class Measuring(Value):
    """A trusted measurement is out; ``submissions`` counts how often it was submitted."""

    kind: Literal["measuring"] = "measuring"
    finding: Finding
    submissions: int = Field(ge=1)


class Interpreting(Value):
    """The trusted evidence is being decoded."""

    kind: Literal["interpreting"] = "interpreting"
    finding: Finding
    evidence: tuple[EvidenceRef, ...]


class Profiling(Value):
    """The advisory profiler turn is out on a candidate that passed."""

    kind: Literal["profiling"] = "profiling"
    finding: Finding


class Exporting(Value):
    """The patch of a candidate that passed is being exported."""

    kind: Literal["exporting"] = "exporting"
    finding: Finding


class Settling(Value):
    """The close of the workspace, with what it asserts, is out."""

    kind: Literal["settling"] = "settling"
    finding: Finding
    refused: int = Field(default=0, ge=0)
    """How many closes core refused; the second proposal is the plain one."""


class Done(Value):
    """The slot is over; the search admits it and drops the child.

    ``settled`` is None for a slot that never held a seat, or whose close core refused: such a
    slot earns no individual.
    """

    kind: Literal["done"] = "done"
    finding: Finding
    settled: SettledSlot | None = None


type SlotPhase = Annotated[
    Queued
    | Opening
    | Mutating
    | Reviewing
    | Measuring
    | Interpreting
    | Profiling
    | Exporting
    | Settling
    | Done,
    Field(discriminator="kind"),
]


class Slot(Value):
    """One slot of a frame: what it was opened to do, its attempt and the phase it is in."""

    key: str = Field(min_length=1)
    plan: SlotPlan
    generation: int = Field(default=0, ge=0)
    """Core's generation of the attempt, known once its workspace opened."""
    phase: SlotPhase = Queued()

    @property
    def attempt(self) -> AttemptId:
        """The attempt the slot runs; a slot is exactly one."""
        return attempt_id(self.key, 1)

    @property
    def ref(self) -> AttemptRef:
        """The exact attempt generation."""
        return AttemptRef(attempt_id=self.attempt, generation=self.generation)

    @property
    def scope(self) -> Scope:
        """The scope the attempt's own work runs in."""
        return Scope(owner=self.attempt, generation=self.generation)

    @property
    def seated(self) -> bool:
        """Whether the slot holds, or has asked for, a seat that is not yet given back."""
        return not isinstance(self.phase, Queued | Done)


@dataclass(frozen=True)
class Env:
    """What a slot reads and never keeps: the policy and the run's view."""

    config: EvolveConfig
    view: RunView


# -- decisions ---------------------------------------------------------------------------------------


def _wait(request: Operation | AskAgent | Measure | StartAttempt | Withdraw, limit: float) -> Wait:
    return Wait(request=request, until=Within(limit=limit))


def _ask(
    slot: Slot, role: Role, prompt: PromptContext, revision: RevisionRef, seconds: float
) -> Decision:
    request = AskAgent(
        decision_id=DecisionId(root=f"{role.value}.0"),
        scope=slot.scope,
        agent=role.value,
        input=prompt,
        revision=revision,
    )
    return _wait(request, seconds)


def _measure(slot: Slot, env: Env, candidate: RevisionRef, index: int) -> Decision:
    names = stages(env.config)
    recipe = measurement_recipe(env.config, env.view.facts)
    plan = recipe.plan(
        candidate,
        purpose="official",
        now_at=env.view.now_at,
        stages=names,
        # Core retries a submission only after an infrastructure failure, and only within
        # this bound, which core's own run limit repeats.
        submission_limit=env.config.max_input_measurement_attempts,
    )
    request = Measure(decision_id=DecisionId(root=f"measure.{index}"), scope=slot.scope, plan=plan)
    return Wait(request=request, until=Within(limit=recipe.budget(names)))


def _operate(
    slot: Slot, env: Env, name: DecisionId, request: InterpretEvidence | ExportPatch
) -> Decision:
    operation = Operation(decision_id=name, scope=slot.scope, request=request)
    return _wait(operation, env.config.operation_seconds)


def _close(slot: Slot, settling: Settling, env: Env) -> Decision:
    """The close that settles the attempt: the full assertion, then the plain one."""
    if settling.refused:
        disposition = Settle(
            assessments=(), eligible=False, retention="discard", outcome="failed", candidate=None
        )
        name = SETTLE_AGAIN
    else:
        disposition = settle_for(slot.plan, settling.finding, env.config)
        name = SETTLE
    request = Withdraw(decision_id=name, scope=THIS_RUN, target=slot.ref, disposition=disposition)
    return Wait(request=request, until=until_deadline(env.view))


# -- failure -----------------------------------------------------------------------------------------


def failure_of(event: Failed, role: Role | None) -> Failure:  # noqa: PLR0911  # LW-230501 [PLR0911]; one return per wait-failure reason keeps the mapping to the typed failure exhaustive and visible.
    """The typed account of a wait that ended without a result."""
    match event.reason:
        case FailureReason.REFUSED:
            return Failure(kind=FailureKind.REFUSED, cause=event.cause)
        case FailureReason.OUT_OF_BUDGET:
            return Failure(kind=FailureKind.OUT_OF_BUDGET)
        case FailureReason.INVALID_REPLY:
            return Failure(kind=FailureKind.INVALID_REPLY, role=role)
        case FailureReason.RENDER_FAILED:
            return Failure(kind=FailureKind.RENDER_FAILED, role=role)
        case FailureReason.WAIT_REFUSED:
            return Failure(kind=FailureKind.WAIT_REFUSED, role=role, refusal=event.refusal)
        case FailureReason.TURN_FAILED:
            return Failure(kind=FailureKind.TURN_FAILED, role=role, turn_failure=event.turn_failure)
        case (
            FailureReason.TIMED_OUT
            | FailureReason.WORKSPACE_CLOSED
            | FailureReason.OWNER_GONE
            | FailureReason.LOST
            | FailureReason.MEASUREMENT_UNTRUSTED
        ):
            return Failure(kind=FailureKind.ENDED, ended=event.reason, role=role)


def _unreachable_of(slot: Slot, failure: Failure) -> Unreachable | None:
    if failure.kind is FailureKind.WAIT_REFUSED and failure.refusal is WaitRefusal.NOT_OFFERED:
        return Unreachable(reason=UnreachableReason.RESUME_CAPABILITY_NOT_OFFERED, subject=slot.key)
    return None


# -- moving between phases -------------------------------------------------------------------------


def _moved(slot: Slot, phase: SlotPhase) -> Slot:
    return slot.model_copy(update={"phase": phase})


def _done(slot: Slot, done: Done) -> tuple[Slot, list[Decision]]:
    return _moved(slot, done), []


def settle(slot: Slot, env: Env, finding: Finding) -> tuple[Slot, list[Decision]]:
    """Close the workspace, asserting what the slot established."""
    settling = Settling(finding=finding)
    return _moved(slot, settling), [_close(slot, settling, env)]


def fail(
    slot: Slot,
    env: Env,
    failure: Failure,
    finding: Finding,
    *,
    unreachable: Unreachable | None = None,
) -> tuple[Slot, list[Decision]]:
    """End the slot with a typed failure; an admitted attempt must still settle."""
    failed = finding.model_copy(update={"failure": failure, "unreachable": unreachable})
    if isinstance(slot.phase, Opening):
        return _done(slot, Done(finding=failed))
    return settle(slot, env, failed)


def _no_answer(
    slot: Slot, env: Env, finding: Finding, failure: Failure
) -> tuple[Slot, list[Decision]]:
    """A role produced no valid answer: the individual keeps the reason as feedback, nothing more."""
    return settle(slot, env, finding.model_copy(update={"note": failure}))


# -- the first decisions of a slot -------------------------------------------------------------------


def skipped(key: str, plan: SlotPlan, failure: Failure) -> Slot:
    """A slot that cannot start; it earns no individual."""
    return Slot(key=key, plan=plan, phase=Done(finding=Finding(failure=failure)))


def seat(
    slot: Slot, brief: MutatePrompt, config: EvolveConfig, within: Within
) -> tuple[Slot, list[Decision]]:
    """Ask core for the slot's attempt.

    ``within`` is how long the request may wait for a seat: as long as the run, since nothing
    but the run bounds a queue.
    """
    request = StartAttempt(
        decision_id=START,
        scope=THIS_RUN,
        attempt_id=slot.attempt,
        item_id=item_id(slot.key, 1),
        workspace=WorkspacePlan(mode=config.workspace_mode, base=slot.plan.base),
        budget=AttemptBudget(
            admission_charge=1,
            # A child does not retry its mutation: a failed child is a failed individual.
            paid_invocation_limit=1,
            retry_limit=config.max_corrections + config.max_turn_drops,
        ),
    )
    return _moved(slot, Opening(brief=brief)), [Wait(request=request, until=within)]


# -- update ---------------------------------------------------------------------------------------


def update(slot: Slot, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    """One event: move to the next phase and say what that phase needs."""
    if isinstance(event, StopRequested):
        return _stop(slot, env)
    return _HANDLERS[slot.phase.kind](slot, slot.phase, event, env)


def _queued(slot: Slot, phase: Queued, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    del slot, env
    raise UnexpectedEvent(phase, event)


def _over(slot: Slot, phase: Done, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    """A late event of a slot that is done and waits only for the lower ones to be admitted."""
    del phase, event, env
    return slot, []


def _carried(phase: SlotPhase) -> Finding:
    """What a phase has established, which a stop settles with."""
    match phase:
        case (
            Reviewing(finding=found)
            | Measuring(finding=found)
            | Interpreting(finding=found)
            | Profiling(finding=found)
            | Exporting(finding=found)
        ):
            return found
        case _:
            return Finding()


def _stop(slot: Slot, env: Env) -> tuple[Slot, list[Decision]]:
    """An operator stop ends an unseated slot at once and settles a seated one.

    It never cancels a turn: the slot settles around it, and whatever the turn later says is
    ignored.
    """
    stopped = Failure(kind=FailureKind.STOPPED)
    match slot.phase:
        case Done() | Settling():
            return slot, []
        case Queued():
            return _done(slot, Done(finding=Finding(failure=stopped)))
        case Opening() as opening:
            return _moved(slot, opening.model_copy(update={"stopping": True})), []
        case _:
            found = _carried(slot.phase).model_copy(update={"failure": stopped})
            return settle(slot, env, found)


def _opening(slot: Slot, phase: Opening, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    match event:
        case WorkspaceOpened(attempt=opened):
            ready = slot.model_copy(update={"generation": opened.generation})
            if phase.stopping:
                return settle(ready, env, Finding(failure=Failure(kind=FailureKind.STOPPED)))
            seconds = env.config.mutator_turn_seconds
            ask = _ask(ready, Role.MUTATOR, phase.brief, slot.plan.base, seconds)
            return _moved(ready, Mutating()), [ask]
        case Failed():
            return fail(slot, env, failure_of(event, None), Finding())
        case _:
            raise UnexpectedEvent(phase, event)


def _failed_turn(
    slot: Slot, env: Env, event: Failed, role: Role, finding: Finding
) -> tuple[Slot, list[Decision]]:
    """A turn that ended without a reply: feedback only if it gave no answer, else a failure."""
    failure = failure_of(event, role)
    if event.reason in NO_ANSWER:
        return _no_answer(slot, env, finding, failure)
    return fail(slot, env, failure, finding, unreachable=_unreachable_of(slot, failure))


def _mutating(slot: Slot, phase: Mutating, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    match event:
        case TurnFinished(reply=MutatorResponse() as reply):
            return _mutated(slot, phase, event, reply, env)
        case Failed():
            return _failed_turn(slot, env, event, Role.MUTATOR, Finding())
        case _:
            raise UnexpectedEvent(phase, event)


def _mutated(
    slot: Slot, phase: Mutating, event: TurnFinished, reply: MutatorResponse, env: Env
) -> tuple[Slot, list[Decision]]:
    """A mutation's reply arrives with core's checkpoint of it, which a mutation always awaits."""
    match event.checkpoint:
        case Declined(reason=declined):
            failure = Failure(kind=FailureKind.CHECKPOINT_DECLINED, declined=declined)
            return fail(slot, env, failure, Finding())
        case None:
            raise UnexpectedEvent(phase, event)
        case retained:
            candidate = retained.revision
    found = Finding(summary=reply.summary, candidate=candidate)
    cited = judged_evidence(env.view, slot.scope)
    prompt = context.judge_prompt(candidate, env.config, cited)
    ask = _ask(slot, Role.JUDGE, prompt, candidate, env.config.judge_turn_seconds)
    return _moved(slot, Reviewing(finding=found)), [ask]


def _reviewing(slot: Slot, phase: Reviewing, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    match event:
        case TurnFinished(reply=JudgeResponse() as verdict, invocation=invocation):
            passed = verdict.verdict is Verdict.PASS
            reviewed = phase.finding.model_copy(
                update={
                    "review_passed": passed,
                    "judge_invocation": invocation,
                    "feedback": verdict.feedback,
                }
            )
            if passed:
                return _after_review(slot, env, reviewed)
            rejected = Failure(kind=FailureKind.REVIEW_REJECTED)
            return settle(slot, env, reviewed.model_copy(update={"note": rejected}))
        case Failed():
            return _failed_turn(slot, env, event, Role.JUDGE, phase.finding)
        case _:
            raise UnexpectedEvent(phase, event)


def _candidate_of(finding: Finding) -> RevisionRef:
    if finding.candidate is None:
        message = "a reviewed candidate has a retained revision"
        raise ValueError(message)
    return finding.candidate


def _after_review(slot: Slot, env: Env, finding: Finding) -> tuple[Slot, list[Decision]]:
    """A passing review: measure the configured gates, else go on to the profile or settle."""
    if stages(env.config):
        measuring = Measuring(finding=finding, submissions=1)
        return _moved(slot, measuring), [_measure(slot, env, _candidate_of(finding), 0)]
    return _after_gates(slot, env, finding)


def _after_gates(slot: Slot, env: Env, finding: Finding) -> tuple[Slot, list[Decision]]:
    """Every configured gate passed: profile the candidate when the run has a profiler."""
    if env.config.profiling:
        candidate = _candidate_of(finding)
        passed = finding.model_copy(update={"passed": True})
        prompt = context.profile_prompt(candidate, env.config)
        ask = _ask(slot, Role.PROFILER, prompt, candidate, env.config.profiler_turn_seconds)
        return _moved(slot, Profiling(finding=passed)), [ask]
    return _finish(slot, env, finding)


def _finish(slot: Slot, env: Env, finding: Finding) -> tuple[Slot, list[Decision]]:
    """A candidate that passed everything: export its patch when the selector ranks by code."""
    passed = finding.model_copy(update={"passed": True})
    if env.config.needs_code:
        export = ExportPatch(revision=_candidate_of(passed))
        return _moved(slot, Exporting(finding=passed)), [_operate(slot, env, EXPORT, export)]
    return settle(slot, env, passed)


def _measuring(slot: Slot, phase: Measuring, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    match event:
        case MeasurementFinished(result=found):
            return _measured(slot, phase, found, env)
        case Failed():
            return fail(slot, env, failure_of(event, None), phase.finding)
        case _:
            raise UnexpectedEvent(phase, event)


def _measured(
    slot: Slot, phase: Measuring, found: MeasurementResult, env: Env
) -> tuple[Slot, list[Decision]]:
    """Record a measurement's trusted evidence for decoding.

    A measurement the machinery interrupted says nothing about the candidate, and one that may
    be either's fault might be a fluke, so both are submitted again while `may_resubmit` allows
    it. Anything else is final: its evidence, failed or not, goes to decoding, so the mutator's
    successors read why. A measurement that left no trusted evidence earns no individual.
    """
    candidate = _candidate_of(phase.finding)
    limit = env.config.max_input_measurement_attempts
    if may_resubmit(found.failure, submissions=phase.submissions, limit=limit):
        again = Measuring(finding=phase.finding, submissions=phase.submissions + 1)
        return _moved(slot, again), [_measure(slot, env, candidate, phase.submissions)]
    keys = trusted_keys(found, scope=found.scope, candidate=candidate, purpose="official")
    if not keys:
        unmeasured = Failure(
            kind=FailureKind.NOT_MEASURED, status=found.status, output=found.diagnostic
        )
        return settle(
            slot, env, phase.finding.model_copy(update={"unmeasured": True, "note": unmeasured})
        )
    refs = tuple(item for item in found.evidence if item.key in keys)
    interpreting = Interpreting(finding=phase.finding, evidence=refs)
    decode = _operate(slot, env, INTERPRET, InterpretEvidence(evidence=refs))
    return _moved(slot, interpreting), [decode]


def _interpreting(
    slot: Slot, phase: Interpreting, event: Event, env: Env
) -> tuple[Slot, list[Decision]]:
    match event:
        case OperationFinished(result=finished):
            return _interpreted(slot, phase, finished.outcome, env)
        case Failed():
            return fail(slot, env, failure_of(event, None), phase.finding)
        case _:
            raise UnexpectedEvent(phase, event)


def _interpreted(
    slot: Slot, phase: Interpreting, outcome: object, env: Env
) -> tuple[Slot, list[Decision]]:
    """Decode the gates' readings: all passing passes the child, a failing gate is its feedback."""
    finding = phase.finding
    if not isinstance(outcome, EvidenceReadings):
        return fail(slot, env, Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), finding)
    readings = accept_readings(outcome.readings, phase.evidence)
    if isinstance(readings, str):
        return fail(slot, env, Failure(kind=FailureKind.EVIDENCE_REFUSED), finding)
    if outcome.status != "succeeded" or not readings:
        return fail(slot, env, Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), finding)
    measured = finding.model_copy(update={"readings": readings})
    for gate in (
        reading_of(readings, EvidenceKind.CORRECTNESS),
        reading_of(readings, EvidenceKind.BENCHMARK),
    ):
        if gate is not None and not gate.passed:
            rejected = Failure(kind=FailureKind.GATE_FAILED, gate=gate.kind)
            update = {"feedback": gate.feedback, "note": rejected}
            return settle(slot, env, measured.model_copy(update=update))
    return _after_gates(slot, env, measured)


def _profiling(slot: Slot, phase: Profiling, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    """The profiler is advisory: a profile that gives no answer costs the candidate nothing."""
    match event:
        case TurnFinished(reply=ProfilerSummary() as summary):
            reading = ProfileReading(
                perf_metric=summary.perf_metric,
                perf_unit=summary.perf_unit,
                metrics=tuple(summary.metrics.items()),
            )
            return _finish(slot, env, phase.finding.model_copy(update={"profile": reading}))
        case Failed():
            failure = failure_of(event, Role.PROFILER)
            if event.reason in NO_ANSWER:
                return _finish(slot, env, phase.finding.model_copy(update={"advisory": failure}))
            unreachable = _unreachable_of(slot, failure)
            return fail(slot, env, failure, phase.finding, unreachable=unreachable)
        case _:
            raise UnexpectedEvent(phase, event)


def _exporting(slot: Slot, phase: Exporting, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    match event:
        case OperationFinished(result=finished):
            outcome = finished.outcome
            if isinstance(outcome, PatchExport) and outcome.status == "succeeded":
                return settle(slot, env, phase.finding.model_copy(update={"patch": outcome.patch}))
            detail = outcome.detail if isinstance(outcome, PatchExport) else ""
            failure = Failure(kind=FailureKind.EXPORT_FAILED, output=detail)
            return fail(slot, env, failure, phase.finding)
        case Failed():
            return fail(slot, env, failure_of(event, None), phase.finding)
        case _:
            raise UnexpectedEvent(phase, event)


def _settling(slot: Slot, phase: Settling, event: Event, env: Env) -> tuple[Slot, list[Decision]]:
    match event:
        case WorkspaceClosed(settlement=settlement):
            return _done(slot, _settled(phase.finding, settlement))
        case Failed(decision=decision) if decision in (SETTLE, SETTLE_AGAIN):
            return _close_refused(slot, phase, event, env)
        case Failed():
            # a late failure of work the settlement already went around: it only fills in why
            late = phase.finding.failure or failure_of(event, None)
            found = phase.finding.model_copy(update={"failure": late})
            return _moved(slot, phase.model_copy(update={"finding": found})), []
        case TurnFinished() | OperationFinished() | MeasurementFinished():
            return slot, []
        case _:
            raise UnexpectedEvent(phase, event)


def _settled(finding: Finding, settlement: Settlement) -> Done:
    """What core settled, and whether the candidate was accepted as the passing one it was."""
    settled = SettledSlot(
        settlement=settlement.settlement_id,
        retained=settlement.candidate,
        eligible=settlement.eligible,
    )
    passed = finding.passed and settlement.eligible and not finding.failed
    note = finding.note
    if finding.passed and not passed and not finding.lesson:
        note = Failure(kind=FailureKind.NOT_ELIGIBLE)
    done = finding.model_copy(update={"passed": passed, "note": note})
    return Done(finding=done, settled=settled)


def _close_refused(
    slot: Slot, phase: Settling, event: Failed, env: Env
) -> tuple[Slot, list[Decision]]:
    """Core would not take the close: propose the plain one, and give up if that fails too.

    A close that nobody takes would leave the slot, and its seat, waiting for ever.
    """
    failure = phase.finding.failure or failure_of(event, None)
    found = phase.finding.model_copy(update={"failure": failure})
    if phase.refused:
        return _done(slot, Done(finding=found))
    plain = Settling(finding=found, refused=1)
    return _moved(slot, plain), [_close(slot, plain, env)]


_HANDLERS: dict[str, Callable[..., tuple[Slot, list[Decision]]]] = {
    "queued": _queued,
    "opening": _opening,
    "mutating": _mutating,
    "reviewing": _reviewing,
    "measuring": _measuring,
    "interpreting": _interpreting,
    "profiling": _profiling,
    "exporting": _exporting,
    "settling": _settling,
    "done": _over,
}
"""The handler of each phase, by its kind: every phase has exactly one."""
