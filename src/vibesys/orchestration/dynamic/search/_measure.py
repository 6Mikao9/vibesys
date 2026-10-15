"""The trusted measurement of a candidate, and what its evidence says.

A workstream does not reserve its measurement up front: it submits the candidate to the root's
evaluation queue and waits until a slot is bound to it (`a_slot`), so its agents' slot goes to new
work meanwhile. Which waiting candidate a free slot measures is the queue's choice (`_queue`).
"""

from vibesys.candidates.api import Baseline
from vibesys.metrics import AcceptedReading, accept_readings
from vibesys.orchestration.dynamic.search._ids import INPUT_MEASURED
from vibesys.orchestration.dynamic.search._operations import EvidenceReadings
from vibesys.orchestration.dynamic.search._outcomes import (
    Evidence,
    Work,
    WorkEndedError,
    WorkstreamStart,
    ended_by,
)
from vibesys.orchestration.dynamic.search._queue import Bound, Dropped, Submission
from vibesys.orchestration.dynamic.search._reports import (
    REPORTS,
    VERDICTS,
    InputOverdue,
    Released,
)
from vibesys.orchestration.dynamic.search._values import (
    Failure,
    FailureKind,
    Proof,
    WorkKind,
)
from vs_core.api.search import (
    Failed,
    Flow,
    MeasurementPurpose,
    MeasurementRecipe,
    RevisionRef,
    TimeUp,
    Workspace,
    trusted_keys,
)

PROFILE_STAGE = "profile"
"""The task's stage that captures a profile."""


def input_so_far(ctx: Flow, w: WorkstreamStart) -> Baseline | None:
    """The input's measurement as known now, without waiting; None while it has not landed."""
    return w.baseline if w.baseline is not None else ctx.latch(INPUT_MEASURED).value


async def the_input(ctx: Flow, w: WorkstreamStart, work: Work) -> Baseline:
    """The input's measurement: known when the workstream started, else waited for here.

    A workstream may start, and its candidate may be reviewed, while the input is still being
    measured. Two things wait for it: the settlement, which compares the candidate with the
    input, and the candidate's trusted measurement. The measurement waits because the backend
    admits jobs in its own order and counts a measurement's deadline from its dispatch: a
    candidate dispatched while the input's job still waits for admission would spend its
    deadline in that queue and settle as failed. It waits once its slot is bound
    (`measure_and_interpret`) until binding moves into the backend (#2662, steps 2 to 4). A wait longer than the run's ``input_wait_seconds`` is reported to the root once, which
    tells the orchestrator on its next turn; the workstream goes on waiting. An input that comes
    back unmeasurable does not stop the candidate: a candidate can fix what broke the input, and
    with no input value to beat it is eligible once it passes the task's gates.
    """
    if work.baseline is None:
        known = input_so_far(ctx, w)
        work.baseline = known if known is not None else await _wait_for_input(ctx, w)
    return work.baseline


async def _wait_for_input(ctx: Flow, w: WorkstreamStart) -> Baseline:
    measured = ctx.latch(INPUT_MEASURED)
    limit = w.config.input_wait_seconds
    if limit is None:
        return await measured
    since = ctx.clock.now()
    match await ctx.first(measured, ctx.timer(since + limit)):
        case TimeUp():
            ctx.channel(REPORTS).send(InputOverdue(key=w.key, since=since, limit=limit))
            return await measured
        case baseline:
            return baseline


async def a_slot(ctx: Flow, w: WorkstreamStart, revision: RevisionRef) -> None:
    """Submit ``revision`` for its trusted measurement and wait until a slot is bound to it.

    The root answers with the queue's verdict: measure now, or never (superseded by a newer
    candidate of the same line, or the queue was full), which ends the work unmeasured.
    """
    ctx.channel(REPORTS).send(
        Submission(
            key=w.key,
            work_id=w.plan.work_id,
            kind=w.plan.kind,
            candidate=revision,
            parent=w.parent,
            at=ctx.clock.now(),
        )
    )
    match await ctx.latch(VERDICTS.of(w.key)):
        case Bound():
            return
        case Dropped(reason=reason, by=by):
            raise WorkEndedError(
                Failure(kind=FailureKind.NOT_MEASURED, dropped=reason, superseded_by=by)
            )


async def measure_and_interpret(
    ctx: Flow, w: WorkstreamStart, ws: Workspace, work: Work, revision: RevisionRef
) -> Evidence:
    """Measure ``revision`` with the trusted evaluations and decode what the evidence says.

    The measurement is dispatched only once a slot is bound to it and the input's measurement
    has landed (`the_input`): the workstream waits for both parked, so its agent slot may go to
    new work meanwhile.

    The backend measures again after an infrastructure failure, so a result that reaches here
    is the candidate's own; a measurement that ended without one is a failure of the workstream.
    """
    purpose = purpose_of(w)
    names = (PROFILE_STAGE,) if w.plan.kind is WorkKind.PROFILE else ctx.task.terms.measured
    recipe = MeasurementRecipe.of(ctx.facts)
    if w.plan.kind is WorkKind.PROFILE and w.plan.required_fields:
        recipe = recipe.requiring(PROFILE_STAGE, w.plan.required_fields)
    await a_slot(ctx, w, revision)
    await the_input(ctx, w, work)  # until a backend binds a slot at its job's start (#2662)
    plan = recipe.plan(revision, purpose=purpose, now_at=ctx.clock.now(), stages=names)
    found = await ctx.evaluation.measure(plan, workspace=ws, within=recipe.budget(names))
    ctx.channel(REPORTS).send(Released(key=w.key))
    if isinstance(found, Failed):
        raise ended_by(found, None)
    keys = trusted_keys(found, scope=found.scope, candidate=revision, purpose=purpose)
    if not keys:
        none = Failure(
            kind=FailureKind.NO_TRUSTED_EVIDENCE, status=found.status, output=found.diagnostic
        )
        raise WorkEndedError(none)
    proof = Proof(refs=tuple(item for item in found.evidence if item.key in keys))
    return Evidence(readings=await interpret(ctx, ws, proof), proof=proof)


async def interpret(ctx: Flow, ws: Workspace, proof: Proof) -> tuple[AcceptedReading, ...]:
    """Have the evidence owner decode the trusted evidence into the readings a settlement needs."""
    seconds = ctx.task.terms.operation_seconds
    outcome = await ctx.evaluation.readings(proof.refs, workspace=ws, within=seconds)
    if isinstance(outcome, Failed):
        raise WorkEndedError(ended_by(outcome, None).failure, proof=proof)
    if not isinstance(outcome, EvidenceReadings):
        raise WorkEndedError(Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), proof=proof)
    readings = accept_readings(outcome.readings, proof.refs)
    if isinstance(readings, str):
        raise WorkEndedError(Failure(kind=FailureKind.EVIDENCE_REFUSED), proof=proof)
    if outcome.status != "succeeded" or not readings:
        raise WorkEndedError(Failure(kind=FailureKind.EVIDENCE_UNDECODABLE), proof=proof)
    return readings


def purpose_of(w: WorkstreamStart) -> MeasurementPurpose:
    """What the measurement is for: a profile is looked at, anything else is scored."""
    return "profile" if w.plan.kind is WorkKind.PROFILE else "official"
