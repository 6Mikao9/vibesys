"""Production resume resolution and shell over durable Fake stores and Fake Git."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar

from tests.support.dynamic_script import FACTS, Executors, config, harness
from tests.vibesys.orchestration.dynamic._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)

from vibesys.orchestration.dynamic import REGISTRATION
from vibesys.orchestration.dynamic.search.api import dynamic
from vibesys.run.project import exact_resume_descriptor, resume_orchestration_decision
from vs_core.api import (
    CancelTurn,
    ControlId,
    ControlInput,
    DiscardWorkspace,
    DispatchTurn,
    EnsureSession,
    EnsureWorkspace,
    ExecutorLifetime,
    ResumeSessionTurn,
    RetainRevision,
    RevisionRef,
    RunControlEvent,
    SnapshotAndRetain,
    StopMode,
    SubmitMeasurement,
    executor_lifetime,
)
from vs_core.api.testing import Failed, Succeeded, Unknown
from vs_dst.api import Effects, RecoverableExecutors
from vs_faults.api import Boundary, FaultGate, FaultPlan, FaultRule, HostFault
from vs_project.api import (
    FakeStateStore,
    NullGitTrackerEvents,
    OrchestrationDescriptor,
    RunEnvironmentRecord,
)
from vs_project.api.testing import FakeGitRepositories, run_execution_record
from vs_runtime.api import EvaluationRetries, OrchestrationResumeDecision, member_workspace_id
from vs_runtime.api.infrastructure import (
    ProjectRunEffects,
    ProjectRunRequest,
    ProjectRunResources,
    RecordState,
    RunEnvironmentSpec,
    classify,
    job_failure,
    open_project_run_resources,
    signal_of,
)
from vs_runtime.api.testing import FakePublicationDelivery, HostOptions, Launch, scripted_host
from vs_sim.api import Signals
from vs_sim.api.testing import FakeSignalSource, ManualClock

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path
    from typing import TextIO

    from pydantic import BaseModel, JsonValue

    from vs_core.api import CoreState, OperationRegistry, Request, SchemaRef
    from vs_core.api.testing import Answer, Journal
    from vs_project.api import CommitOutcome, OrchestrationRunManifest, StoredEnvelope, StoreFence
    from vs_runtime.api.core import CoreRuntime, ExecutionContext, ExecutionResult
    from vs_runtime.api.testing import Script, ScriptedHost
    from vs_sim.api.testing import VirtualClock


class Fault(StrEnum):
    CRASH = "crash"
    SIGTERM = "sigterm"
    STALL = "stall"
    ROLLBACK = "rollback"
    INFRASTRUCTURE = "infrastructure"
    PROVIDER = "provider"
    """The agent provider errors: the next turns never answer and the first cancel of one hangs."""
    WEDGED_STOP = "wedged-stop"
    """The agent provider wedges: every turn, cancel and close stays unanswered. The operator
    stops the host ``delay`` durable points later, after the turns outlasted their limits, and
    kills it ``grace`` points after that, so what it left in flight is begun and never sealed."""


PROVIDER_OUTAGE = 2
"""Agent turns a provider outage swallows: within every role's asks again (`lost_turns`)."""


class Point(StrEnum):
    COMMIT = "commit"
    RECEIPT = "receipt"


class DurableStore(FakeStateStore):
    """Commit faults use the common FaultPlan; stalls advance only virtual time."""

    def __init__(self, fault: Fault, at: int, boundary: Point, delay: int, grace: int) -> None:
        super().__init__()
        self.at = at
        self.delay = delay
        self.boundary = boundary
        self.receipts = 0
        self.fault = fault
        self.commits = 0
        self.clock: VirtualClock | None = None
        self.signals = FakeSignalSource()
        self.stopping = False
        self.refusing = False
        self.outage = 0
        self.hung_cancels = 0
        self.wedged = False
        self.gate = FaultGate(
            FaultPlan(
                seed=at,
                rules=(
                    FaultRule(
                        boundary=Boundary.DURABLE_WRITE,
                        target=boundary.value,
                        # A stopped host that the provider wedged is killed ``grace`` durable
                        # points after its stop, as an operator kills one that outlasts its grace.
                        at=at + delay + grace if fault is Fault.WEDGED_STOP else at,
                        fault=HostFault.CRASH_AFTER,
                    ),
                )
                if fault in (Fault.CRASH, Fault.ROLLBACK, Fault.WEDGED_STOP)
                else (),
            )
        )

    def commit(
        self, expected_revision: int | None, envelope: StoredEnvelope, fence: StoreFence, now: float
    ) -> CommitOutcome:
        self.commits += 1
        self.crossing(Point.COMMIT, self.commits)
        return self.gate.around(
            Boundary.DURABLE_WRITE,
            Point.COMMIT.value,
            lambda: super(DurableStore, self).commit(expected_revision, envelope, fence, now),
        )

    def journal(self, result: ExecutionResult) -> ExecutionResult:
        """The execution receipt is sealed before its acknowledgement reaches core."""
        self.receipts += 1
        self.crossing(Point.RECEIPT, self.receipts)
        return self.gate.around(Boundary.DURABLE_WRITE, Point.RECEIPT.value, lambda: result)

    def crossing(self, boundary: Point, ordinal: int) -> None:
        """Fault decisions use durable ordinals, never scheduler timing."""
        if boundary is not self.boundary:
            return
        if self.fault is Fault.WEDGED_STOP:
            self.wedge(ordinal)
            return
        if ordinal != self.at:
            return
        if self.fault is Fault.STALL:
            assert self.clock is not None
            self.clock.at += 84
        elif self.fault is Fault.SIGTERM:
            self.stopping = True
        elif self.fault is Fault.INFRASTRUCTURE:
            self.refusing = True
        elif self.fault is Fault.PROVIDER:
            self.outage, self.hung_cancels = PROVIDER_OUTAGE, 1

    def wedge(self, ordinal: int) -> None:
        """The provider wedges, and the operator stops the host once the turns outlasted it."""
        if ordinal == self.at:
            self.wedged = True
        elif ordinal == self.at + self.delay:
            self.stopping = True

    def provider_silence(self, request: Request) -> Unknown | None:
        """Silence for an agent call the outage swallows: no reply ever arrives.

        Core ends a swallowed turn at its limit, cancels it and asks again as the role says;
        the same outage hangs the first cancellation of a lost turn.
        """
        if (
            self.wedged
            and executor_lifetime(request) is ExecutorLifetime.INSTANCE
            and not isinstance(request, EnsureSession)
        ):
            return Unknown()
        if isinstance(request, DispatchTurn | ResumeSessionTurn) and self.outage:
            self.outage -= 1
            return Unknown()
        if (
            isinstance(request, CancelTurn)
            and self.hung_cancels
            and request.request_id is not None
            and not request.request_id.root.endswith("close")
        ):
            self.hung_cancels -= 1
            return Unknown()
        return None

    def attach(self, shell: CoreRuntime, clock: VirtualClock) -> None:
        self.clock = clock

        def stop() -> None:
            shell.submit(
                RunControlEvent(
                    control=ControlInput(
                        control_id=ControlId(root="sigterm"), action="stop", mode=StopMode.CANCEL
                    ),
                    now_at=clock.now(),
                ),
                now_at=clock.now(),
            )

        self.signals.add_handler(Signals.SIGTERM, stop)


class ResumeWorld:
    """A scripted dynamic flow that really creates, snapshots and discards Fake Git trees.

    ``width`` is the run's ``--max-in-flight`` and ``resume_width`` the one its resume plans;
    ``backlog`` and ``resume_backlog`` are the same for ``--max-backlog`` (None: its default, as
    many as the width).
    """

    width: ClassVar[int] = 1
    resume_width: ClassVar[int] = 1
    backlog: ClassVar[int | None] = None
    resume_backlog: ClassVar[int | None] = None

    def __init__(
        self,
        root: Path,
        *,
        extra: int,
        fault: Fault,
        at: int,
        boundary: Point = Point.COMMIT,
        delay: int = 0,
        grace: int = 0,
    ) -> None:
        root.mkdir()
        (root / "candidate.py").write_text("VALUE = 1\n")
        self.git = FakeGitRepositories()
        self.store = DurableStore(fault, at, boundary, delay, grace)
        self.effects = Effects()
        self.delivery = FakePublicationDelivery()
        self.rounds = 1
        self.extra = extra
        self.refusals = 0
        self.outages = 0
        self.request = ProjectRunRequest(
            project_root=root,
            run_id="crash-resume",
            display_name="crash resume",
            task_name=None,
            existing=False,
            framework_version="test",
            run_environment=RunEnvironmentRecord(name="docker"),
            execution=run_execution_record(),
            orchestration=self.descriptor(self.rounds, self.width, self.backlog),
        )
        self.resources: ProjectRunResources | None = None
        widest = max(self.width, self.resume_width)
        self.script = Executors(
            planner=deque(
                plan_reply(*(implement(f"h{n}-{m}") for m in range(widest)))
                for n in range(self.rounds + extra)
            ),
            implementer=deque(implemented() for _ in range((self.rounds + extra) * widest)),
            judge=deque(reviewed() for _ in range((self.rounds + extra) * widest)),
            exhausted="not json",
        )
        self.workspaces: dict[str, str] = {}

    @staticmethod
    def descriptor(
        rounds: int, width: int = 1, backlog: int | None = None
    ) -> OrchestrationDescriptor:
        options: dict[str, JsonValue] = {
            "interface": "inprocess",
            "max_rounds": rounds,
            "max_in_flight": width,
        }
        if backlog is not None:
            options["max_backlog"] = backlog
        return OrchestrationDescriptor(id="dynamic", config_version=1, options=options)

    def open(self, *, resume: bool) -> ProjectRunResources:
        if resume:
            # The host that stopped is dead: its provider, and a signal it had not yet seen, with it.
            self.store.wedged = self.store.stopping = False
        requested = (
            self.descriptor(self.rounds + self.extra, self.resume_width, self.resume_backlog)
            if resume
            else self.descriptor(self.rounds, self.width, self.backlog)
        )
        policy = REGISTRATION.resume_policy or exact_resume_descriptor

        def resolve(recorded: OrchestrationRunManifest) -> OrchestrationResumeDecision:
            return resume_orchestration_decision(
                recorded,
                requested,
                RunEnvironmentSpec(name="docker"),
                self.request.execution,
                policy,
            )

        effects = ProjectRunEffects(
            git_events=NullGitTrackerEvents(),
            git_repository=self.git.repository,
            clock=ManualClock(),
            log_emit=_emit,
            on_log_ready=lambda _path: None,
            state_stores=lambda _project, _run: self.store,
        )
        self.resources = open_project_run_resources(
            replace(self.request, existing=resume, orchestration=requested),
            effects=effects,
            resolve_resume=resolve,
        )
        return self.resources

    def host(self, *, resume: bool, now: float) -> ScriptedHost:
        resources = self.resources
        assert resources is not None
        selected = (
            config(
                max_rounds=self.rounds + self.extra,
                max_in_flight=self.resume_width,
                max_backlog=self.resume_backlog,
            )
            if resume
            else config(max_rounds=self.rounds, max_in_flight=self.width, max_backlog=self.backlog)
        )
        baseline = resources.git.trusted_input_baseline
        assert baseline is not None
        facts = FACTS.model_copy(update={"baseline": RevisionRef.of_git_commit(baseline)})

        host = scripted_host(
            Launch(dynamic, selected),
            self,
            harness(selected, facts=facts),
            dynamic.reply_schemas,
            HostOptions(translator=self.translator, attach=self.store.attach, resumed=resume),
            store=self.store,
            delivery=self.delivery,
            clock_at=now,
        )
        host.config = replace(host.config, lease_duration=60)
        return host

    def translator(
        self,
        script: Script,
        core: Callable[[], CoreState],
        registry: OperationRegistry,
        schemas: Mapping[SchemaRef, type[BaseModel]],
        journal: Journal,
    ) -> RecoverableExecutors:
        store = self.store

        class JournalExecutors(RecoverableExecutors):
            async def execute(self, request: Request, context: ExecutionContext) -> ExecutionResult:
                return store.journal(await super().execute(request, context))

        return JournalExecutors(script, core, registry, schemas, journal, self.effects)

    def __call__(self, request: Request, core: CoreState) -> Answer:
        resources = self.resources
        assert resources is not None
        if self.store.stopping:
            self.store.stopping = False
            self.store.signals.deliver(Signals.SIGTERM)
        if isinstance(request, SubmitMeasurement) and self.store.refusing:
            self.store.refusing = False
            self.refusals += 1
            # The trusted evaluator refused for disk pressure (protocol exit 75).
            # Classification is production's table; the fake does not assign blame.
            failure = job_failure(
                [classify(signal_of(75, trusted_evaluator=True), RecordState.OUTCOME)]
            )
            retries = EvaluationRetries(infrastructure_attempts=2, ambiguous_attempts=1)
            if not retries.allows_another(failure, tries=1):
                return Failed(accepted=False, measurement_failure=failure)
            # The backend owns retries; only its final try reaches core. The
            # scripted far side heals on try two, as the production wrapper does.
            return self.script(request, core)
        if (silence := self.store.provider_silence(request)) is not None:
            self.outages += isinstance(request, DispatchTurn | ResumeSessionTurn)
            return silence
        if isinstance(request, EnsureWorkspace):
            key = member_workspace_id(request.scope.owner.root)
            base = request.plan.base.git_commit
            assert base is not None
            candidate = resources.open_candidate(key, base)
            candidate.retain(base)
            candidate.detach()
            self.workspaces[key] = base
        elif isinstance(request, SnapshotAndRetain):
            key = member_workspace_id(request.scope.owner.root)
            candidate = resources.reattach_candidate(key, self.workspaces[key])
            assert candidate is not None
            (candidate.project_root / "candidate.py").write_text(
                f"VALUE = {request.scope.owner.root!r}\n"
            )
            candidate.git.snapshot("candidate")
            revision = candidate.git.current_sha()
            assert revision is not None
            candidate.retain(revision)
            candidate.detach()
            self.workspaces[key] = revision
            return Succeeded(revision=RevisionRef.of_git_commit(revision))
        elif isinstance(request, RetainRevision):
            resources.git.retain_candidate(
                member_workspace_id(request.scope.owner.root), request.revision.revision_id.root
            )
        elif isinstance(request, DiscardWorkspace):
            resources.remove_leftover(member_workspace_id(request.scope.owner.root))
            self.workspaces.pop(member_workspace_id(request.scope.owner.root), None)
        return self.script(request, core)


class WideWorld(ResumeWorld):
    """A run two agents wide, resumed two wide."""

    width = 2
    resume_width = 2


class NarrowedWorld(ResumeWorld):
    """A run two attempts wide whose resume plans one: ``--max-in-flight 1 --max-backlog 0``."""

    width = 2
    resume_width = 1
    resume_backlog = 0


class WidenedWorld(ResumeWorld):
    """A run one attempt wide, with no backlog, whose resume plans two with the default backlog."""

    width = 1
    backlog = 0
    resume_width = 2


def _emit(text: str, writer: TextIO) -> None:
    writer.write(text + "\n")
