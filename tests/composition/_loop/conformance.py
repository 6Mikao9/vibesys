"""What a search must satisfy to run on the vs-core lifecycle, over the production shell.

A `Subject` describes one search to the suite: how to build a fresh launch of it, the harness
that hosts it, and a generator of scripted worlds (what the planner, agents and evaluators
answer). The properties in this module (`check_*`) are written against that description only, so
a ported search registers one `Subject` in `subjects.py` and inherits every property. Nothing
here names a concrete search.

Replay determinism and the codec round trip of a search's state are the runtime's own rules and
are held by `libs/vs-core/tests/test_flows_restart.py` and `test_flows_codec.py`; what is checked
here is what only a whole run shows: liveness, limits, the winner the framework adopts, faults at
the executors and at delivery, and a stop at any request. The crash tier lives in
`test_conformance_crash.py`, on vs-dst.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from vs_core.api import (
    MAX_SUBMISSIONS_PER_MEASUREMENT,
    AdoptRevision,
    ControlId,
    ControlInput,
    DispatchTurn,
    MeasurementFailure,
    ObserveOwnedJob,
    OwnedJob,
    ResourceId,
    ResumeSessionTurn,
    RetainedCandidate,
    RunControlEvent,
    StopMode,
    SubmitMeasurement,
    TrustedBaseline,
    VerifyAdoption,
    project,
)
from vs_core.testing.drive import Failed, Faults, Retryable, Succeeded, Unknown, drive
from vs_core.testing.liveness import Budget, End, Journal, assert_live
from vs_runtime.api.testing import HostOptions, Launch, Run, drive_shell

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from hypothesis.strategies import SearchStrategy
    from pydantic import BaseModel

    from vs_core.api import CoreState, Request, RevisionRef, SchemaRef
    from vs_core.testing.drive import Answer, Harness
    from vs_runtime.api.core import CoreRuntime


class World(Protocol):
    """The far side of a run: executors that answer every request, and what they measured."""

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """What the executors report for ``request``."""
        ...

    def headline(self, commit: str) -> float | None:
        """The trusted headline metric (larger is better) of a candidate commit, if measured."""
        ...


@dataclass(frozen=True)
class Subject:
    """One search as the conformance suite sees it."""

    name: str
    launch: Callable[[], Launch[Any]]
    """A fresh launch of the search with its config; every call builds an equal one."""
    harness: Harness
    """Registry, run facts, limits, envelope type and evidence requirements of the run."""
    schemas: Mapping[SchemaRef, type[BaseModel]]
    """The reply types the run's turns answer."""
    worlds: SearchStrategy[Callable[[], World]]
    """Factories of fresh scripted worlds: agents reply, measurements succeed or fail."""
    default_world: Callable[[], World]
    """A fixed world in which the search runs its full course and adopts a candidate."""
    smallest_world: Callable[[], World]
    """The shortest full run (fewest durable commits), which the crash sweep uses."""
    issued: frozenset[str]
    """Request kinds the search's own steps cause in a run that has no fault."""
    start_budget: int
    """How many attempts the search's config lets it start."""
    sweeps_crashes: bool = True
    """Whether the crash sweep (C2) runs on this subject: at a sample of its durable boundaries
    in CI, at every one under `--crash-points=all`."""


# Kinds core issues only to recover from a fault, to cancel, or to resume a suspended turn.
RECOVERY_ONLY = frozenset(
    {
        "inspect_request",
        "inspect_turn",
        "inspect_owned_job",
        "cancel_turn",
        "cancel_owned_job",
        "cancel_owned_resource",
        "resume_session_turn",
        "collect_evidence",
        "snapshot_and_retain_run",
        "restore_revision",
    }
)


# -- generated faults over any world --------------------------------------------------------


@dataclass(frozen=True)
class Outcomes:
    """How each measurement submission and agent turn of a run goes, in order of occurrence.

    The last outcome of a list repeats; an empty list means every one succeeds.
    """

    measurements: tuple[str, ...] = ()
    turns: tuple[str, ...] = ()
    adoption_fails: bool = False


MEASUREMENT_OUTCOMES = ("succeeded", "workload_failure", "infrastructure_failure")
TURN_OUTCOMES = ("replies", "unparsable")
LOST_MEASUREMENT = "lost"
LOST_TURN = "lost"
RETRYABLE_TURN = "retryable_failure"


def _nth(outcomes: tuple[str, ...], position: int, default: str) -> str:
    return default if not outcomes else outcomes[min(position, len(outcomes) - 1)]


class Faulted:
    """A world whose measurements and turns go the way `Outcomes` says, over a base world.

    With ``stop_at`` the operator's stop arrives while the ``stop_at``-th request executes,
    the way a control arrives mid-run in production (the shell admits it between steps).
    """

    def attach(self, shell: CoreRuntime[Any], clock: object) -> None:
        """Hear the shell once it exists: the operator's stop is submitted to it, as a control is."""
        del clock
        self._shell = shell

    def __init__(self, base: World, outcomes: Outcomes, *, stop_at: int | None = None) -> None:
        """Layer ``outcomes`` over ``base``."""
        self._base = base
        self._outcomes = outcomes
        self._stop_at = stop_at
        self._shell: CoreRuntime[Any] | None = None
        self._requests = 0
        self._submitted = 0
        self._asked = 0
        self._infrastructure: set[str] = set()
        self.stopped = False
        self.executed: Counter[str] = Counter()

    def headline(self, commit: str) -> float | None:
        """The base world's headline."""
        return self._base.headline(commit)

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """Answer from the generated outcome when there is one, else from the base world."""
        if request.request_id is not None:
            self.executed[request.request_id.root] += 1
        self._maybe_stop(core)
        faulted = (
            self._measurement(request)
            or self._job(request, core)
            or self._turn(request)
            or self._adoption(request)
        )
        return self._base(request, core) if faulted is None else faulted

    def _maybe_stop(self, core: CoreState) -> None:
        position, self._requests = self._requests, self._requests + 1
        if self._stop_at != position or self._shell is None:
            return
        self.stopped = True
        self._shell.submit(
            RunControlEvent(
                control=ControlInput(
                    control_id=ControlId(root="stop"), action="stop", mode=StopMode.DRAIN
                ),
                now_at=core.run.now_at,
            ),
            now_at=core.run.now_at,
        )

    def _measurement(self, request: Request) -> Answer | None:
        if not isinstance(request, SubmitMeasurement):
            return None
        outcome = _nth(self._outcomes.measurements, self._submitted, "succeeded")
        self._submitted += 1
        if outcome == "infrastructure_failure":
            self._infrastructure.add(request.plan.model_dump_json())
        return {
            "workload_failure": Failed(MeasurementFailure.WORKLOAD),
            LOST_MEASUREMENT: Unknown(),
        }.get(outcome)

    def _job(self, request: Request, core: CoreState) -> Answer | None:
        if not isinstance(request, ObserveOwnedJob):
            return None
        job = next(
            row
            for row in core.evaluation.jobs
            if isinstance(row, OwnedJob) and row.resource_id == request.resource_id
        )
        return Failed(accepted=True) if job.plan.model_dump_json() in self._infrastructure else None

    def _turn(self, request: Request) -> Answer | None:
        if not isinstance(request, DispatchTurn | ResumeSessionTurn):
            return None
        outcome = _nth(self._outcomes.turns, self._asked, "replies")
        self._asked += 1
        lease = ResourceId(root=f"lease:{request.turn.session.session_id.root}")
        return {
            "unparsable": Succeeded(output_json="not json", resource_id=lease),
            RETRYABLE_TURN: Retryable(),
            LOST_TURN: Unknown(),
        }.get(outcome)

    def _adoption(self, request: Request) -> Answer | None:
        if self._outcomes.adoption_fails and isinstance(request, AdoptRevision | VerifyAdoption):
            return Failed()
        return None


# -- running a subject ------------------------------------------------------------------------


@dataclass
class Recorded:
    """One finished run and the world it ran against."""

    run: Run
    world: World


def run_subject(
    subject: Subject,
    world: World,
    *,
    host: HostOptions | None = None,
    live: bool = True,
) -> Recorded:
    """Run a fresh launch of ``subject`` to the end of its run on the production shell.

    The run must satisfy the liveness invariants unless ``live`` is False.
    """
    finished = drive_shell(subject.launch(), world, subject.harness, subject.schemas, host)
    if live:
        assert_live(
            finished.journal,
            finished.core,
            Budget(retries=subject.harness.limits.request_retries.limit),
        )
    if finished.halted is not None:
        raise finished.halted
    return Recorded(finished, world)


# -- properties ---------------------------------------------------------------------------------


def check_limits_hold(subject: Subject, recorded: Recorded) -> None:
    """C6: the run never asked for more than its limits or than its config lets it start."""
    limits = subject.harness.limits
    view = project(recorded.run.core)
    assert len(view.attempts) <= limits.max_attempts
    assert len(view.attempts) <= subject.start_budget
    submissions = Counter(
        row.plan.model_dump_json()
        for row in recorded.run.core.evaluation.jobs
        if isinstance(row, OwnedJob)
    )
    assert all(count <= MAX_SUBMISSIONS_PER_MEASUREMENT for count in submissions.values())


def check_adoption_is_safe(recorded: Recorded) -> None:
    """C8: the adopted winner is an eligible retained candidate with the best headline.

    The oracle is independent of the search: eligibility and retention come from core's
    settlements and the ranking from the world's own measurements. The input stands only when
    no candidate settled eligible, and a run that ended without a result adopts nothing.
    """
    core = recorded.run.core
    result = core.run.result
    if result is None or result.selection is None:
        return
    eligible = [
        row for row in core.settlement.settlements if row.eligible and row.retention == "candidate"
    ]
    selection = result.selection
    if isinstance(selection, TrustedBaseline):
        assert not eligible, "the input was kept while an eligible candidate existed"
        return
    assert isinstance(selection, RetainedCandidate)
    by_id = {row.settlement_id: row for row in core.settlement.settlements}
    chosen = by_id[selection.settlement_id]
    assert chosen.eligible, "an ineligible settlement was adopted"
    assert chosen.retention == "candidate", "a settlement that retained nothing was adopted"
    scores = {row.settlement_id: _headline(recorded.world, row.candidate) for row in eligible}
    best = max((score for score in scores.values() if score is not None), default=None)
    assert scores[chosen.settlement_id] == best, "a better eligible candidate existed"


def _headline(world: World, candidate: RevisionRef | None) -> float | None:
    return None if candidate is None else world.headline(candidate.revision_id.root)


def run_pure(
    subject: Subject, world: World, faults: Faults, *, end: End = End.CUT_SHORT
) -> list[str]:
    """Drive a fresh search on the pure driver under delivery ``faults``; the decisions it submitted, as JSON.

    The pure driver answers no agent turn (its sessions need the shell's owner events), so
    a world that reaches one is cut short there; the run must still be live up to it.
    """
    launch = subject.launch()
    driver = launch.search.driver(launch.config, subject.harness.registry)
    trace = drive(driver, launch.config, world, subject.harness, faults)
    assert_live(
        Journal.from_log(trace.log),
        trace.core,
        Budget(retries=subject.harness.limits.request_retries.limit),
        end,
    )
    return [decision.model_dump_json() for decision in trace.decisions]


# -- request-kind recovery matrix (C9) ----------------------------------------------------------

CONCLUSIVE_AFTER_FAULT = frozenset(
    {
        *(
            (kind, how)
            for kind in ("adopt_revision", "dispatch_turn", "submit_measurement")
            for how in ("unknown_once", "retryable_once", "silent")
        ),
        *(
            (kind, how)
            for kind in ("execute_registered_operation", "verify_adoption")
            for how in ("unknown_once", "retryable_once")
        ),
    }
)
"""Kind and answer pairs after which a run ends live, whatever strategy issues the kind.

For the other pairs core blocks the intent at its bound but routes the block to the strategy
only, so the attempt, session or settlement waiting on it never concludes and the run stays
open. That gap belongs to vs-core (see the dynamic recovery matrix); when it is fixed these
pairs join the set and every strategy is asked to survive them.
"""
KIND_FAULTS = ("unknown_once", "retryable_once", "silent")


class KindFault:
    """A world that answers the requests of one kind a faulty way and the rest as ``base``."""

    def __init__(self, base: World, kind: str, how: str) -> None:
        """Fault ``kind``: once (``unknown_once``, ``retryable_once``) or every time (``silent``)."""
        self._base = base
        self._kind = kind
        self._how = how
        self._seen = 0
        self.executed: Counter[str] = Counter()

    def headline(self, commit: str) -> float | None:
        """The base world's headline."""
        return self._base.headline(commit)

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """The faulty answer for the chosen kind, else the base world's."""
        if request.request_id is not None:
            self.executed[request.request_id.root] += 1
        if request.kind == self._kind:
            position, self._seen = self._seen, self._seen + 1
            if self._how == "silent" or position == 0:
                return Retryable() if self._how == "retryable_once" else Unknown()
        return self._base(request, core)
