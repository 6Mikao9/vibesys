"""A scripted minimal search for the composition skeleton, written as a flow.

It plays one scenario: measure the baseline, open one attempt, ask the implementer for
one change, measure the revision that turn produced, keep it as the attempt's candidate,
and let the framework adopt the winner and end the run. It holds no lifecycle or accounting
state of its own; the flow is the whole script.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from pydantic import BaseModel

import vs_core.api as core
from vs_core.api.search import (
    INPUT,
    Assessment,
    Charge,
    Failed,
    Flow,
    NotOpened,
    NoWinner,
    Role,
    TurnFinished,
    search,
)

if TYPE_CHECKING:
    from vs_core.api import OperationRegistry

ATTEMPT = core.AttemptRef(attempt_id=core.AttemptId(root="attempt-0"), generation=0)
DIGEST = "ab" * 32


class Implementation(BaseModel):
    """The implementer's structured reply: the commit it made in its workspace."""

    commit: str
    waiting: bool = False


IMPLEMENTER = core.IdScheme("skeleton").role_id("implementer")


class Prompt(core.Value):
    """What the implementer is asked: the context of the skeleton's one prompt."""


class Render(core.RenderRoleArtifactsBase[Prompt]):
    """Render one role prompt."""

    kind: Literal["skeleton.render_role_artifacts"] = "skeleton.render_role_artifacts"


class Verify(core.VerifyParentRevisionBase):
    """Verify a parent revision."""

    kind: Literal["skeleton.verify_parent_revision"] = "skeleton.verify_parent_revision"


class Readings(core.EvidenceReadingsBase[core.Value]):
    """No readings: the skeleton never interprets evidence."""


class Interpret(core.InterpretEvidenceBase):
    """Decode evidence."""

    kind: Literal["skeleton.interpret_evidence"] = "skeleton.interpret_evidence"
    outcome_model: ClassVar[type[BaseModel]] = Readings


OPERATIONS = core.StrategyOperations("skeleton", Render, Verify, Interpret)


IMPLEMENTER_ROLE = Role(
    name="implementer",
    prompt="skeleton.j2",
    reply=Implementation,
    access=core.Access.WRITE_CANDIDATE,
    workspace=core.WorkspaceMode.ISOLATED_CHILD,
    charge=Charge.ATTEMPT,
    turn_time=400.0,
    corrections=0,
    lost_turns=0,
)
IMPLEMENTATION = IMPLEMENTER_ROLE.reply_schema

_RUN_DEADLINE = 1000.0
"""When the skeleton world's run ends (the run clock, in seconds)."""
_MEASURE_SECONDS = 880.0


class SkeletonConfig(core.Value):
    """Which scenario the skeleton plays."""

    measured: bool = True
    keeps_candidate: bool = True
    cancels_after_start: bool = False
    probe: bool = False
    fails: bool = False
    """End at once with no winner: a run that only has to start and finish."""


def measurement(
    candidate: core.RevisionRef, purpose: Literal["baseline", "official"], *, now_at: float = 0.0
) -> core.MeasurementPlan:
    """An accuracy-then-benchmark plan of one exact revision, ``now_at`` seconds into the clock."""
    return core.MeasurementPlan(
        purpose=purpose,
        candidate=candidate,
        evaluator_digest=DIGEST,
        workload_digest=DIGEST,
        environment_digest=DIGEST,
        stages=tuple(
            core.MeasurementStage(stage_id=name, execution_budget=10.0)
            for name in ("accuracy", "benchmark")
        ),
        policy="ordered",
        recipe=core.ArtifactRef(artifact_id=core.ArtifactId(root="recipe"), digest=DIGEST),
        submitted_at=now_at,
        queue_allowance=880.0,
        # Absolute run-clock time, past any restart the scenarios make (the run ends at 1000 s).
        accuracy_stage="accuracy",
        accuracy_gated=(core.EvidenceKind.CORRECTNESS, core.EvidenceKind.BENCHMARK),
    )


def _origin(ctx: Flow, config: SkeletonConfig) -> float:
    """The clock time the plans' deadlines count from.

    Zero, so a plan is a pure function of the run's state, except for the probe, which is
    driven on a real clock far from zero.
    """
    return ctx.clock.now() if config.probe else 0.0


async def _measure(
    ctx: Flow,
    config: SkeletonConfig,
    revision: core.RevisionRef,
    purpose: Literal["baseline", "official"],
    workspace: core.Workspace | None = None,
) -> NoWinner | None:
    """Measure ``revision``; a failed measurement is the reason the run has no winner."""
    plan = measurement(revision, purpose, now_at=_origin(ctx, config))
    match await ctx.evaluation.measure(plan, workspace=workspace, within=_MEASURE_SECONDS):
        case Failed() as failed:
            return NoWinner(reason=f"measurement {failed.reason}")
        case core.MeasurementResult(failure=failure) if failure is not None:
            return NoWinner(reason=f"measurement {failure.value}")
    return None


async def _probe(ctx: Flow, config: SkeletonConfig) -> None:
    """Submit the input-baseline measurement, then wait for the run to be ended."""
    async with ctx.group(limit=1) as group:
        group.start("baseline", _measure, config, ctx.facts.baseline, "baseline")
        await ctx.first(ctx.stopped)


async def _attempt(ctx: Flow, config: SkeletonConfig) -> NoWinner | None:
    """One attempt: an implementer turn, its measurement, and the candidate it keeps."""
    baseline = ctx.facts.baseline
    async with ctx.workspaces.open(
        attempt=ATTEMPT.attempt_id,
        item=core.ItemId(root="item-0"),
        plan=core.WorkspacePlan(mode=core.WorkspaceMode.ISOLATED_CHILD, base=baseline),
        budget=core.AttemptBudget(),
    ) as workspace:
        if isinstance(workspace, NotOpened):
            return NoWinner(reason="the attempt workspace did not open")
        if config.cancels_after_start:
            # Wait for the stop; the timer is what keeps the shell from calling the wait a stall.
            await ctx.first(ctx.stopped, ctx.timer(_RUN_DEADLINE))
            return None
        session = ctx.agents.session(IMPLEMENTER_ROLE, workspace=workspace, revision=baseline)
        match await session.turn(Prompt()):
            case TurnFinished(reply=Implementation(commit=commit)) if commit:
                candidate = core.RevisionRef.of_git_commit(commit)
            case _:
                return NoWinner(reason="the implementer turn produced no commit")
        if config.measured and (
            refused := await _measure(ctx, config, candidate, "official", workspace)
        ):
            return refused
        if config.keeps_candidate:
            # Eligible only with accepted evidence for the candidate, which core checks.
            ctx.workspaces.keep(
                workspace, candidate, Assessment(outcome="succeeded", eligible=True)
            )
    return None


@search(
    "skeleton",
    config=SkeletonConfig,
    operations=OPERATIONS,
    roles=(IMPLEMENTER_ROLE,),
)
async def skeleton(ctx: Flow, config: SkeletonConfig) -> NoWinner | None:
    """Play the scenario ``config`` names; the framework adopts the chosen winner."""
    if config.fails:
        return NoWinner(reason="scripted")
    if config.probe:
        if config.measured:
            await _probe(ctx, config)
        return None
    if config.measured and (refused := await _measure(ctx, config, ctx.facts.baseline, "baseline")):
        return refused
    if (refused := await _attempt(ctx, config)) is not None:
        return refused
    # A retained candidate wins only when core made its settlement eligible, which it does only
    # with accepted evidence for that candidate. Without a measurement the input is all there is.
    eligible = [kept for kept in ctx.workspaces.kept() if kept.settlement.eligible]
    ctx.choose(eligible[0] if eligible else INPUT)
    return None


DECLARATION = skeleton.declaration


class SkeletonStrategy(core.Value):
    """The scripted scenario; ``measured=False`` omits both measurements.

    The unmeasured variant skips the baseline and candidate measurements, so the
    workspace, session, retirement and adoption interfaces can be driven without the
    measurement ones. Its settlement keeps the candidate but is ineligible (no evidence),
    so the run adopts the trusted baseline.
    """

    config: SkeletonConfig = SkeletonConfig()

    @property
    def declaration(self) -> core.SearchDeclaration:
        """What the host must offer this scenario."""
        return skeleton.declaration

    @property
    def measured(self) -> bool:
        """Whether the scenario measures the baseline and the candidate."""
        return self.config.measured

    @classmethod
    def unmeasured(cls, *, keeps_candidate: bool = True) -> SkeletonStrategy:
        """The scenario without the baseline and candidate measurements.

        With ``keeps_candidate=False`` the attempt is discarded and the trusted
        baseline is adopted, which needs no retained revision.
        """
        return cls(config=SkeletonConfig(measured=False, keeps_candidate=keeps_candidate))

    @classmethod
    def cancelled(cls) -> SkeletonStrategy:
        """Open one attempt and wait there until the run is stopped (the deadline, here).

        No session or measurement is involved: the attempt's workspace is acquired and
        then discarded by the stop, and the run ends with no adopted revision.
        """
        return cls(config=SkeletonConfig(measured=False, cancels_after_start=True))

    @classmethod
    def failing(cls) -> SkeletonStrategy:
        """End at once with no winner, touching no agent, workspace or measurement."""
        return cls(config=SkeletonConfig(fails=True))

    @classmethod
    def baseline_probe(cls) -> SkeletonStrategy:
        """Submit the input-baseline measurement once, then wait for the run to be ended.

        It is a run that is always waiting on a job: for tests of what ends such a run (a
        signal, an operator stop) and what the host then cancels.
        """
        return cls(config=SkeletonConfig(probe=True))

    def driver(self, registry: OperationRegistry | None = None) -> core.SearchDriver:
        """The driver that plays this scenario."""
        return skeleton.driver(self.config, registry)
