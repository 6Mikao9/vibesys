"""What the dynamic search knows of its run, and the plain decisions that follow from it.

`RunRecord` is the root flow's one piece of mutable state. The flows it starts report back
through `planned` and `finished`, which fold their results in; nothing else writes it. It holds
the facts (hypotheses, finished work, what is running, the parents on offer) and answers the
questions the root flow asks of them (may a plan start, is the search over, which plans become
workstreams). Anything that needs the run's view lives in `_parents`, anything about how a
record changes in `_hypotheses`. It is rebuilt by replay after a restart, so it is never saved.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from vibesys.candidates import api as candidates
from vibesys.orchestration.dynamic.flow import _hypotheses
from vibesys.orchestration.dynamic.flow._outcomes import (
    WorkstreamDone,
    WorkstreamStart,
)
from vibesys.orchestration.dynamic.search.api import (
    DynamicConfig,
    FinishedWork,
    HypothesisRecord,
    ParentOption,
    ParentSnapshot,
    PlanCheck,
    PlanProblem,
    PlanWorld,
    Unreachable,
    WorkKind,
    WorkPlan,
    free_slots,
    planning_key,
    remaining,
    workstream_key,
)
from vs_core.api.search import RevisionRef, Value

type ParentOf = Callable[[WorkPlan], tuple[RevisionRef, str | None]]
"""Which revision a plan builds on, and the hypothesis it descends from."""


@dataclass(frozen=True)
class Scheduled:
    """A planning call ended with a plan to schedule."""

    check: PlanCheck


@dataclass(frozen=True)
class NoPlan:
    """A planning call ended with nothing valid.

    ``stuck`` says nothing is in flight to wait for, so planning has failed for good;
    ``blocked_at_done`` is the number of finished workstreams planning waits to exceed.
    """

    problem: PlanProblem | None
    stuck: bool
    blocked_at_done: int


type PlanOutcome = Scheduled | NoPlan


@dataclass
class PlannerGate:
    """Whether planning may start again, and why the last call ended empty."""

    failed: bool = False
    """The last call ended with nothing valid and nothing in flight to wait for."""
    blocked_at_done: int | None = None
    """Finished workstreams when a call scheduled nothing; planning resumes after another one."""
    last_error: PlanProblem | None = None
    """Shown in the first prompt of the next call."""


class Running(Value):
    """A workstream in flight, as the published record shows it."""

    sequence: int
    plan: WorkPlan


class RunSnapshot(Value):
    """What the search publishes of its record, for the projector and for tests.

    Read it from the persisted state with `FlowSearch.published(state, RunSnapshot)`.
    """

    kind: Literal["dynamic-run-snapshot"] = "dynamic-run-snapshot"
    baseline: candidates.Baseline
    hypotheses: tuple[HypothesisRecord, ...] = ()
    finished: tuple[FinishedWork, ...] = ()
    running: tuple[Running, ...] = ()
    parents: tuple[ParentSnapshot, ...] = ()
    unreachable: tuple[Unreachable, ...] = ()
    winner: candidates.Winner | None = None


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
    accepted: list[WorkPlan] = field(default_factory=list)
    """Plans a planning call accepted, not yet started as workstreams."""
    parents: tuple[ParentSnapshot, ...] = ()
    """The candidates the planner may build on."""
    withheld: tuple[str, ...] = ()
    """Parent revisions whose verification failed; never offered to the planner again."""
    refunded: int = 0
    """Workstreams refunded because they never opened."""
    unreachable: list[Unreachable] = field(default_factory=list)
    gate: PlannerGate = field(default_factory=PlannerGate)
    calls: int = 0
    """Planning calls begun; the next call's key is numbered by it."""

    # -- what the run has left -----------------------------------------------------------------

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
        if stopping or gate.failed or not self.capacity():
            return False
        return gate.blocked_at_done is None or len(self.finished_work) > gate.blocked_at_done

    def over(self, *, planning: bool, stopping: bool) -> bool:
        """Whether nothing is in flight and nothing more will be scheduled."""
        if self.running or planning or self.accepted:
            return False
        left = remaining(self.config, started=self.started, refunded=self.refunded)
        return stopping or self.gate.failed or left == 0

    # -- planning ------------------------------------------------------------------------------

    def next_call(self) -> str:
        """Begin a planning call; its key."""
        self.calls += 1
        self.gate.blocked_at_done = None
        return planning_key(self.calls)

    def world(self, offered: tuple[ParentOption, ...]) -> PlanWorld:
        """What a planning call reads of the run now, with the parents on offer."""
        return PlanWorld(
            config=self.config,
            base=self.base,
            baseline=self.baseline,
            hypotheses=tuple(self.hypotheses.values()),
            running=tuple(self.running.values()),
            finished=tuple(self.finished_work),
            refunded=self.refunded,
            offered=offered,
        )

    def planned(self, outcome: PlanOutcome) -> None:
        """Fold a finished planning call: park or abandon what it decided, queue what it accepted."""
        match outcome:
            case Scheduled(check=check):
                for change in check.updates:
                    known = self.hypotheses[change.hypothesis_id]
                    self.hypotheses[change.hypothesis_id] = _hypotheses.with_disposition(
                        known, change
                    )
                self.gate.last_error = None
                self.gate.blocked_at_done = None
                self.accepted.extend(check.accepted)
            case NoPlan(problem=problem, stuck=stuck, blocked_at_done=at_done):
                self.gate = PlannerGate(failed=stuck, blocked_at_done=at_done, last_error=problem)

    # -- workstreams ---------------------------------------------------------------------------

    def ready_to_start(self, free: int, parent_of: ParentOf) -> list[WorkstreamStart]:
        """A workstream for each accepted plan that fits in ``free`` slots, now running."""
        starts = []
        ready, self.accepted = self.accepted[:free], self.accepted[free:]
        for plan in ready:
            sequence = self.started + 1
            parent, lineage = parent_of(plan)
            record = self.hypotheses.get(plan.work_id)
            prior = record.rounds[-1] if record is not None and record.rounds else None
            if plan.kind is WorkKind.IMPLEMENT:
                self.hypotheses[plan.work_id] = _hypotheses.scheduled(record, plan, sequence)
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

    def finished(self, done: WorkstreamDone) -> None:
        """Keep what a finished workstream leaves behind: its record, round and parent."""
        start = done.start
        del self.running[start.key]
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
        current = self.hypotheses.get(start.plan.work_id)
        if current is not None and not profile and done.settlement is not None:
            self.hypotheses[start.plan.work_id] = _hypotheses.with_round(current, done)

    def stopped_before_start(self, key: str) -> None:
        """A workstream the stop ended before it had an attempt."""
        self.finished(WorkstreamDone.stopped(self.running[key]))

    # -- the record ----------------------------------------------------------------------------

    def snapshot(self, winner: candidates.Winner | None = None) -> RunSnapshot:
        """The record as it is published."""
        return RunSnapshot(
            baseline=self.baseline,
            hypotheses=tuple(self.hypotheses.values()),
            finished=tuple(self.finished_work),
            running=tuple(Running(sequence=r.sequence, plan=r.plan) for r in self.running.values()),
            parents=self.parents,
            unreachable=tuple(self.unreachable),
            winner=winner,
        )
