"""Production resume resolution and shell over durable Fake stores and Fake Git."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from enum import StrEnum
from typing import TYPE_CHECKING

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
    ControlId,
    ControlInput,
    DiscardWorkspace,
    EnsureWorkspace,
    RetainRevision,
    RevisionRef,
    RunControlEvent,
    SnapshotAndRetain,
    StopMode,
    SubmitMeasurement,
)
from vs_core.api.testing import Failed, Succeeded
from vs_dst.api import Effects, RecoverableExecutors
from vs_faults.api import Boundary, FaultGate, FaultPlan, FaultRule, HostFault
from vs_project.api import (
    FakeStateStore,
    NullGitTrackerEvents,
    OrchestrationDescriptor,
    RunEnvironmentRecord,
)
from vs_project.api.testing import FakeGitRepositories, run_execution_record
from vs_runtime.api import EvaluationRetries, member_workspace_id
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

    from pydantic import BaseModel

    from vs_core.api import CoreState, OperationRegistry, Request, SchemaRef
    from vs_core.api.testing import Answer, Journal
    from vs_project.api import CommitOutcome, OrchestrationRunManifest, StoredEnvelope, StoreFence
    from vs_runtime.api import OrchestrationResumeDecision
    from vs_runtime.api.core import CoreRuntime, ExecutionContext, ExecutionResult
    from vs_runtime.api.testing import Script, ScriptedHost
    from vs_sim.api.testing import VirtualClock


class Fault(StrEnum):
    CRASH = "crash"
    SIGTERM = "sigterm"
    STALL = "stall"
    ROLLBACK = "rollback"
    INFRASTRUCTURE = "infrastructure"


class Point(StrEnum):
    COMMIT = "commit"
    RECEIPT = "receipt"


class DurableStore(FakeStateStore):
    """Commit faults use the common FaultPlan; stalls advance only virtual time."""

    def __init__(self, fault: Fault, at: int, boundary: Point) -> None:
        super().__init__()
        self.at = at
        self.boundary = boundary
        self.receipts = 0
        self.fault = fault
        self.commits = 0
        self.clock: VirtualClock | None = None
        self.signals = FakeSignalSource()
        self.stopping = False
        self.refusing = False
        self.gate = FaultGate(
            FaultPlan(
                seed=at,
                rules=(
                    FaultRule(
                        boundary=Boundary.DURABLE_WRITE,
                        target=boundary.value,
                        at=at,
                        fault=HostFault.CRASH_AFTER,
                    ),
                )
                if fault in (Fault.CRASH, Fault.ROLLBACK)
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
        if boundary is not self.boundary or ordinal != self.at:
            return
        if self.fault is Fault.STALL:
            assert self.clock is not None
            self.clock.at += 84
        elif self.fault is Fault.SIGTERM:
            self.stopping = True
        elif self.fault is Fault.INFRASTRUCTURE:
            self.refusing = True

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
    """A scripted dynamic flow that really creates, snapshots and discards Fake Git trees."""

    def __init__(
        self,
        root: Path,
        *,
        extra: int,
        fault: Fault,
        at: int,
        boundary: Point = Point.COMMIT,
    ) -> None:
        root.mkdir()
        (root / "candidate.py").write_text("VALUE = 1\n")
        self.git = FakeGitRepositories()
        self.store = DurableStore(fault, at, boundary)
        self.effects = Effects()
        self.delivery = FakePublicationDelivery()
        self.rounds = 1
        self.extra = extra
        self.refusals = 0
        self.request = ProjectRunRequest(
            project_root=root,
            run_id="crash-resume",
            display_name="crash resume",
            task_name=None,
            existing=False,
            framework_version="test",
            run_environment=RunEnvironmentRecord(name="docker"),
            execution=run_execution_record(),
            orchestration=self.descriptor(self.rounds),
        )
        self.resources: ProjectRunResources | None = None
        self.script = Executors(
            planner=deque(plan_reply(implement(f"h{n}")) for n in range(self.rounds + extra)),
            implementer=deque(implemented() for _ in range(self.rounds + extra)),
            judge=deque(reviewed() for _ in range(self.rounds + extra)),
            exhausted="not json",
        )
        self.workspaces: dict[str, str] = {}

    @staticmethod
    def descriptor(rounds: int) -> OrchestrationDescriptor:
        return OrchestrationDescriptor(
            id="dynamic",
            config_version=1,
            options={"interface": "inprocess", "max_rounds": rounds, "max_in_flight": 1},
        )

    def open(self, *, resume: bool) -> ProjectRunResources:
        requested = self.descriptor(self.rounds + self.extra if resume else self.rounds)
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
        selected = config(max_rounds=self.rounds + self.extra if resume else self.rounds)
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


def _emit(text: str, writer: TextIO) -> None:
    writer.write(text + "\n")
