"""The parent's live record of the run: what the search knows besides its running children.

`RunRecord` is plain mutable state that the root flow owns. The flows it spawns report back
through `planned` and `finished`, which fold their results into it; nothing else writes it.
Everything that follows from the record (is there a slot to plan for, is the search over, which
workstream does an accepted plan become) is a method here, so the root flow reads as a story.
It is rebuilt by replay after a restart, so it is never persisted.
"""

from dataclasses import dataclass, field
from functools import partial

from pydantic import TypeAdapter

from vibesys.candidates import api as candidates
from vibesys.hypothesis import RunEnding
from vibesys.orchestration.dynamic.flow._outcomes import (
    WorkstreamDone,
    WorkstreamStart,
    round_of,
    snapshot_of,
)
from vibesys.orchestration.dynamic.search.api import (
    DynamicConfig,
    FinishedWork,
    HypothesisRecord,
    ParentConflictError,
    ParentOption,
    ParentSnapshot,
    PlanCheck,
    PlanProblem,
    PlanWorld,
    RoundRecord,
    Unreachable,
    WorkKind,
    WorkPlan,
    free_slots,
    ingest,
    offered_parents,
    planning_key,
    remaining,
    resolve,
    workstream_key,
)
from vs_core.api.search import Flow, RevisionRef, RunView


@dataclass(frozen=True)
class Scheduled:
    """A planning call ended with a plan to schedule."""

    check: PlanCheck


@dataclass(frozen=True)
class GaveUp:
    """A planning call ended with nothing valid.

    ``stuck`` says nothing is in flight to wait for, so planning has failed for good;
    ``blocked_at_done`` is the number of finished workstreams planning waits to exceed.
    """

    problem: PlanProblem | None
    stuck: bool
    blocked_at_done: int


type PlanOutcome = Scheduled | GaveUp


@dataclass
class PlannerGate:
    """Whether planning may start again, and why the last call ended empty."""

    failed: bool = False
    """The last call ended with nothing valid and nothing in flight to wait for."""
    blocked_at_done: int | None = None
    """Finished workstreams when a call scheduled nothing; planning resumes after another one."""
    last_error: PlanProblem | None = None
    """Shown in the first prompt of the next call."""


_OFFERS = TypeAdapter(tuple[ParentOption, ...])
_RESOLVED = TypeAdapter(ParentSnapshot | None)
_CATALOG = TypeAdapter(tuple[ParentSnapshot, ...] | None)


@dataclass
class RunRecord:
    """Hypotheses, finished work and parents of one dynamic run, and the slots left to fill."""

    config: DynamicConfig
    base: RevisionRef
    """The revision the run started from."""
    baseline: candidates.Baseline
    hypotheses: dict[str, HypothesisRecord] = field(default_factory=dict)
    finished_work: list[FinishedWork] = field(default_factory=list)
    running: dict[str, WorkstreamStart] = field(default_factory=dict)
    parents: tuple[ParentSnapshot, ...] = ()
    refunded: int = 0
    """Workstreams refunded because they never opened."""
    withheld: tuple[str, ...] = ()
    """Parent revisions whose verification failed; never offered to the planner again."""
    unreachable: list[Unreachable] = field(default_factory=list)
    gate: PlannerGate = field(default_factory=PlannerGate)
    calls: int = 0
    """Planning calls opened; the next call's key is numbered by it."""
    planning: bool = False
    accepted: list[WorkPlan] = field(default_factory=list)
    """Plans a planning call accepted, not yet started as workstreams."""
    new_parents: list[ParentSnapshot] = field(default_factory=list)
    """Candidates settled since the catalog was last updated."""
    changed: bool = False
    """Something the root flow acts on happened since it last looked."""

    # -- slots ---------------------------------------------------------------------------------

    @property
    def started(self) -> int:
        """Workstreams ever scheduled: the finished ones and the ones still running."""
        return len(self.finished_work) + len(self.running)

    def capacity(self) -> int:
        """Free slots the next plan may fill, bounded by the remaining budget."""
        left = remaining(self.config, started=self.started, refunded=self.refunded)
        return free_slots(self.config, active=len(self.running), left=left)

    def may_plan(self, *, stopping: bool) -> bool:
        """Whether a planning call should start: a slot is free and planning is not held off."""
        gate = self.gate
        if stopping or gate.failed or self.planning or not self.capacity():
            return False
        return gate.blocked_at_done is None or len(self.finished_work) > gate.blocked_at_done

    def over(self, *, stopping: bool) -> bool:
        """Whether nothing is in flight and nothing more will be scheduled."""
        if self.running or self.planning or self.accepted:
            return False
        left = remaining(self.config, started=self.started, refunded=self.refunded)
        return stopping or self.gate.failed or left == 0

    # -- planning ------------------------------------------------------------------------------

    def begin_planning(self) -> str:
        """Open a planning call; its key."""
        self.calls += 1
        self.planning = True
        self.gate.blocked_at_done = None
        return planning_key(self.calls)

    def world(self, ctx: Flow) -> PlanWorld:
        """What a planning call reads of the run now."""
        return PlanWorld(
            config=self.config,
            base=self.base,
            baseline=self.baseline,
            hypotheses=tuple(self.hypotheses.values()),
            running=tuple(self.running.values()),
            finished=tuple(self.finished_work),
            refunded=self.refunded,
            offered=self._offered(ctx),
        )

    def _offered(self, ctx: Flow) -> tuple[ParentOption, ...]:
        if not self.parents:
            return ()  # nothing to offer, and the first plan is asked before a view exists
        return ctx.read(partial(self._offer_in, self.parents, self.withheld), _OFFERS)

    @staticmethod
    def _offer_in(
        parents: tuple[ParentSnapshot, ...], withheld: tuple[str, ...], view: RunView
    ) -> tuple[ParentOption, ...]:
        return offered_parents(parents, withheld, view)

    def planned(self, outcome: PlanOutcome) -> None:
        """Fold a finished planning call: park or abandon what it decided, queue what it accepted."""
        self.planning = False
        self.changed = True
        match outcome:
            case Scheduled(check=check):
                for change in check.updates:
                    self.hypotheses[change.hypothesis_id] = self.hypotheses[
                        change.hypothesis_id
                    ].model_copy(
                        update={
                            "strategy": change.disposition,
                            "reason": change.reason,
                            "reason_kind": change.reason_kind,
                        }
                    )
                self.gate.last_error = None
                self.gate.blocked_at_done = None
                self.accepted.extend(check.accepted)
            case GaveUp(problem=problem, stuck=stuck, blocked_at_done=at_done):
                self.gate = PlannerGate(failed=stuck, blocked_at_done=at_done, last_error=problem)

    # -- starting workstreams --------------------------------------------------------------------

    def take_starts(self, ctx: Flow) -> list[WorkstreamStart]:
        """One workstream for each plan the planner accepted, now running."""
        starts = []
        accepted, self.accepted = self.accepted, []
        for plan in accepted:
            sequence = self.started + 1
            parent, lineage = self._parent_of(ctx, plan)
            prior = self._prior_round(plan)
            self._note_hypothesis(plan, sequence)
            start = WorkstreamStart(
                key=workstream_key(sequence, plan.work_id),
                sequence=sequence,
                plan=plan,
                parent=parent,
                parent_hypothesis_id=lineage,
                prior=prior,
                config=self.config,
                baseline=self.baseline,
            )
            self.running[start.key] = start
            starts.append(start)
        return starts

    def _parent_of(self, ctx: Flow, plan: WorkPlan) -> tuple[RevisionRef, str | None]:
        """Freeze the parent revision a workstream builds on, resolved only from offers."""
        prior = self.hypotheses.get(plan.work_id)
        if plan.kind is WorkKind.IMPLEMENT and plan.continue_hypothesis and prior is not None:
            last = prior.rounds[-1] if prior.rounds else None
            if last is not None and last.candidate is not None:
                return last.candidate, prior.lineage_parent_id
            earlier = [item for item in self.finished_work if item.plan.work_id == plan.work_id]
            if earlier:
                return earlier[-1].parent, earlier[-1].parent_hypothesis_id
        if plan.parent_hypothesis_id is not None:
            found = ctx.read(partial(self._resolve_in, self.parents, plan), _RESOLVED)
            if found is not None:
                return found.revision, plan.parent_hypothesis_id
        return self.base, None

    @staticmethod
    def _resolve_in(
        parents: tuple[ParentSnapshot, ...], plan: WorkPlan, view: RunView
    ) -> ParentSnapshot | None:
        assert plan.parent_hypothesis_id is not None  # noqa: S101  # LW-220427 [S101]; the caller checked it.
        return resolve(parents, view, plan.parent_hypothesis_id, plan.parent_revision)

    def _prior_round(self, plan: WorkPlan) -> RoundRecord | None:
        record = self.hypotheses.get(plan.work_id)
        return record.rounds[-1] if record is not None and record.rounds else None

    def _note_hypothesis(self, plan: WorkPlan, sequence: int) -> None:
        if plan.kind is not WorkKind.IMPLEMENT:
            return
        known = self.hypotheses.get(plan.work_id)
        self.hypotheses[plan.work_id] = (
            known.model_copy(update={"last_task": plan.task})
            if known is not None
            else HypothesisRecord(
                hypothesis_id=plan.work_id,
                title=plan.title,
                hypothesis=plan.hypothesis,
                first_sequence=sequence,
                lineage_parent_id=plan.parent_hypothesis_id,
                last_task=plan.task,
            )
        )

    # -- what a finished workstream leaves behind ------------------------------------------------

    def finished(self, done: WorkstreamDone) -> None:
        """Keep what a finished workstream leaves behind: its record, round and parent."""
        start = done.start
        del self.running[start.key]
        self.changed = True
        profile = start.plan.kind is WorkKind.PROFILE
        self.finished_work.append(
            FinishedWork(
                sequence=start.sequence,
                plan=start.plan,
                parent=start.parent,
                parent_hypothesis_id=start.parent_hypothesis_id,
                failure=done.failure,
                readings=done.evidence.readings if profile else (),
                proof=done.evidence.proof if profile else None,
            )
        )
        self.refunded += int(done.refunded)
        if done.withheld is not None:
            root = done.withheld.revision_id.root
            if root not in self.withheld:
                self.withheld = (*self.withheld, root)
            self.gate.blocked_at_done = None
        if done.unreachable is not None:
            self.unreachable.append(done.unreachable)
        if profile or done.settlement is None:
            return
        self._add_round(done)
        snapshot = snapshot_of(done)
        if snapshot is not None:
            self.new_parents.append(snapshot)

    def _add_round(self, done: WorkstreamDone) -> None:
        plan = done.start.plan
        current = self.hypotheses.get(plan.work_id)
        if current is None:
            return
        self.hypotheses[plan.work_id] = current.model_copy(
            update={
                "rounds": (*current.rounds, round_of(done)),
                "continuation_rounds": current.continuation_rounds
                + (1 if plan.continue_hypothesis else 0),
            }
        )

    def publish_parents(self, ctx: Flow) -> None:
        """Offer the candidates settled since last time as parents, if core can prove them."""
        pending, self.new_parents = self.new_parents, []
        for snapshot in pending:
            catalog = ctx.read(partial(_ingested, self.parents, snapshot), _CATALOG)
            if catalog is not None:
                self.parents = catalog

    # -- what the search ends with ------------------------------------------------------------------

    def eligible_rounds(self) -> tuple[candidates.EligibleRound, ...]:
        """Every winner-eligible round of the run."""
        return tuple(
            candidates.EligibleRound(
                hypothesis_id=hypothesis.hypothesis_id,
                sequence=row.sequence,
                settlement=row.settlement,
                candidate=row.candidate,
                metrics=row.metrics,
            )
            for hypothesis in self.hypotheses.values()
            for row in hypothesis.rounds
            if row.eligible and row.candidate is not None and row.settlement is not None
        )

    def no_winner_reason(self) -> str:
        """Why the run ends without a winner, for the result it proposes."""
        problem = self.gate.last_error
        asked = problem.kind.value if problem is not None else ""
        if candidates.trusted(self.baseline) or self.eligible_rounds():
            return asked or "no candidate could be adopted"
        why = candidates.failure(self.baseline)
        detail = why.kind.value if why is not None else "the input did not pass its measurement"
        if asked:
            detail = f"{detail}; the planner's last reply: {asked}"
        return candidates.ending_reason(RunEnding.NO_TRUSTED_RESULT, detail)


def _ingested(
    catalog: tuple[ParentSnapshot, ...], snapshot: ParentSnapshot, view: RunView
) -> tuple[ParentSnapshot, ...] | None:
    """The catalog with ``snapshot`` published, or None when it conflicts with a published one."""
    try:
        return ingest(catalog, snapshot, view)
    except ParentConflictError:
        return None
