"""A toy search on core's agent pipeline, and the scripted world around it (test support).

The search is the smallest one that exercises what core owns of agent turns: it opens a
workspace per workstream, asks an implementer (a write turn) and then a judge (a read-only turn)
in it, closes the workspace and finishes. Both roles may end a turn by waiting for an evaluation
they submitted. Whatever core says (a refusal, a failed turn, a wait that is refused), the search
settles its workspace plainly and goes on, so a run that does not end is core's doing.

`ToyHost` plays everything outside core: it answers each request the way an executor would, and
lets the turns that wait submit their evaluation through core's agent-call admission (the event
the production bridge admits). It never decides what core concludes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, ClassVar, Literal

import vs_core.api as core
from vs_core.testing.drive import (
    Answer,
    Faults,
    Harness,
    Running,
    Succeeded,
    Trace,
    drive,
)

from .test_measurements import plan as measurement_plan

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

type Ending = Literal["wait", "result", "invalid"]
ENDINGS: tuple[Ending, ...] = ("wait", "result", "invalid")
"""How a turn that validated its wait can end: the waiting reply, its final result, junk."""

ROLES = ("implementer", "judge")
IDS = core.IdScheme("toy", role_prefix="toy-")


# -- the operations and agents the search declares ---------------------------------------------


class Prompt(core.Value):
    """What a toy agent is asked."""

    text: str


class Result(core.Value):
    """What an implementer answers."""

    summary: str


class Verdict(core.Value):
    """What a judge answers."""

    passed: bool


class ImplementerReply(core.WaitableReply[Result]):
    """An implementer turn ends with a result or a wait for evaluations."""


class JudgeReply(core.WaitableReply[Verdict]):
    """A judge turn ends with a verdict or a wait for evaluations."""


class Render(core.RenderRoleArtifactsBase[Prompt]):
    """Render one role prompt."""

    kind: Literal["toy.render_role_artifacts"] = "toy.render_role_artifacts"


class Verify(core.VerifyParentRevisionBase):
    """Verify a parent revision."""

    kind: Literal["toy.verify_parent_revision"] = "toy.verify_parent_revision"


class Readings(core.EvidenceReadingsBase[core.Value]):
    """No readings: the toy search never interprets evidence."""


class Interpret(core.InterpretEvidenceBase):
    """Decode evidence."""

    kind: Literal["toy.interpret_evidence"] = "toy.interpret_evidence"
    outcome_model: ClassVar[type[BaseModel]] = Readings


OPERATIONS = core.StrategyOperations("toy", Render, Verify, Interpret)


def agents() -> tuple[core.Agent, ...]:
    """The two roles: the implementer writes a candidate, the judge only reads it."""
    retry = core.Retry(corrections=1, lost_turns=1)
    return (
        core.Agent(
            role="implementer",
            reply=ImplementerReply,
            schema=core.SchemaRef(name="toy.implementer", version=1),
            access=core.Access.WRITE_CANDIDATE,
            workspace=core.WorkspaceMode.ISOLATED_CHILD,
            charge="paid",
            retry=retry,
            waits=True,
            checkpoint=lambda reply: isinstance(reply, Result),
        ),
        core.Agent(
            role="judge",
            reply=JudgeReply,
            schema=core.SchemaRef(name="toy.judge", version=1),
            access=core.Access.READ_ONLY,
            workspace=core.WorkspaceMode.READ_ONLY_REVISION,
            charge="free",
            retry=retry,
            waits=True,
        ),
    )


def _resume(facts: core.ResumeFacts) -> core.Value:
    return Prompt(text=f"resume {facts.agent}")


PROTOCOL = core.AgentProtocol(
    correction=lambda role, errors: Prompt(text=f"correct {role} {len(errors)}"),
    resume=_resume,
    wait_unrecorded=lambda role: Prompt(text=f"wait unrecorded {role}"),
)

# -- the search ----------------------------------------------------------------------------------


class Config(core.Value):
    """How many workstreams the toy search runs, and how many at once."""

    workstreams: int = 1
    in_flight: int = 1


class Done(core.Value):
    """What a workstream ended with: which asks were answered, and how its workspace closed."""

    key: str
    asked: tuple[str, ...] = ()
    """The roles whose ask ended in a reply."""
    failed: tuple[str, ...] = ()
    """The roles whose ask ended in a typed failure."""
    opened: bool = True
    closed: bool = False


class Summary(core.Value):
    """What the search published: every workstream's end."""

    done: tuple[Done, ...] = ()


DECLARATION = core.SearchDeclaration(
    strategy_id=core.StrategyId(root="toy"),
    state_schema=core.SchemaRef(name="toy.flow-state", version=1),
    optional=frozenset({"suspend"}),
    uses=frozenset({core.WorkKind.ASK_AGENT, core.WorkKind.START_ATTEMPT, core.WorkKind.WITHDRAW}),
    handles=frozenset(
        {
            core.EventKind.WORKSPACE_OPENED,
            core.EventKind.WORKSPACE_CLOSED,
            core.EventKind.TURN_FINISHED,
            core.EventKind.STOP_REQUESTED,
            core.EventKind.FAILED,
        }
    ),
)
_PLAIN = core.Settle(
    assessments=(), eligible=False, retention="discard", outcome="failed", candidate=None
)
_LIMIT = core.Within(limit=10_000.0)


async def workstream(ctx: core.Flow, key: str, number: int) -> Done:
    """Open a workspace, ask the implementer then the judge, close the workspace."""
    opened = await ctx.wait(
        core.StartAttempt(
            decision_id=core.DecisionId(root="start"),
            scope=core.THIS_RUN,
            attempt_id=core.AttemptId(root=key),
            item_id=core.ItemId(root=key),
            workspace=core.WorkspacePlan(
                mode=core.WorkspaceMode.ISOLATED_CHILD, base=ctx.facts.baseline
            ),
            budget=core.AttemptBudget(admission_charge=1, paid_invocation_limit=6, retry_limit=3),
        ),
        _LIMIT,
    )
    if not isinstance(opened, core.WorkspaceOpened):
        return Done(key=key, opened=False)
    ref = opened.attempt
    scope = core.Scope(owner=ref.attempt_id, generation=ref.generation)
    asked: list[str] = []
    failed: list[str] = []
    revision = ctx.facts.baseline
    for role, reply in (("implementer", Result), ("judge", Verdict)):
        event = await ctx.wait(
            core.AskAgent(
                decision_id=core.DecisionId(root=role),
                scope=scope,
                agent=role,
                input=Prompt(text=f"{role} {number}"),
                revision=revision,
            ),
            _LIMIT,
            reply=reply,
        )
        match event:
            case core.TurnFinished(checkpoint=core.Retained(revision=retained)):
                revision = retained
                asked.append(role)
            case core.TurnFinished():
                asked.append(role)
            case core.Failed():
                failed.append(role)
                break
    closed = await ctx.wait(
        core.Withdraw(
            decision_id=core.DecisionId(root="settle"),
            scope=core.THIS_RUN,
            target=ref,
            disposition=_PLAIN,
        ),
        _LIMIT,
    )
    return Done(
        key=key,
        asked=tuple(asked),
        failed=tuple(failed),
        closed=isinstance(closed, core.WorkspaceClosed),
    )


async def root(ctx: core.Flow, config: Config) -> None:
    """Run the workstreams ``in_flight`` at a time, then finish."""
    started = 0
    done: list[Done] = []
    async with ctx.group(limit=config.in_flight) as group:
        while len(done) < config.workstreams:
            while started < config.workstreams and group.free:
                group.start(f"w{started}", workstream, f"w{started}", started)
                started += 1
            ended = await ctx.first(group)
            assert isinstance(ended, Done)
            done.append(ended)
    ctx.publish(Summary(done=tuple(done)))
    await ctx.finish(
        core.Finish(
            decision_id=core.DecisionId(root="done"),
            scope=core.THIS_RUN,
            mode="drain",
            result=core.RunResultProposal(outcome="success", reason="all workstreams ended"),
        )
    )


def search(config: Config) -> core.AgentTurns:
    """The toy search wrapped in core's agent pipeline."""
    return core.AgentTurns(
        core.FlowSearch(DECLARATION, root, config, OPERATIONS.registry()),
        agents=agents(),
        protocol=PROTOCOL,
        operations=OPERATIONS,
        ids=IDS,
        inputs=Prompt,
    )


# -- the world around core ------------------------------------------------------------------------

FACTS = core.RunFacts(
    objective="make it faster",
    baseline=core.RevisionRef.of_git_commit("abc123"),
    evaluator_digest="evaluator",
    workload_digest="workload",
    environment_digest="environment",
)
LIMITS = core.Limits(max_attempts=8, max_turns=60, max_parallel=4, max_retries=2, max_refunds=2)


def revision_of(attempt: str) -> core.RevisionRef:
    """The commit the workspace executor retains for an attempt (fixed by the attempt)."""
    return core.RevisionRef.of_git_commit(hashlib.sha256(attempt.encode()).hexdigest()[:40])


def waiting_reply(handles: Sequence[str] = ("h",)) -> str:
    """The reply of a turn that waits for the evaluations it submitted."""
    return json.dumps({"kind": "waiting_for_evaluation", "handles": list(handles)})


def _final(role: str) -> str:
    return json.dumps({"summary": "done"} if role == "implementer" else {"passed": True})


def _lease(session_id: core.SessionId) -> core.ResourceId:
    return core.ResourceId(root=f"lease:{session_id.root}")


@dataclass
class ToyHost:
    """Scripted executors for one run.

    ``waits`` says, for the n-th fresh turn of a role in ``roles`` (cycling), whether it
    submits an evaluation of its candidate and ends waiting for it; ``polls`` how many polls the
    n-th job reports running; ``ends`` how the n-th waiting turn ends once it validated its wait.
    ``shares`` makes every turn of an attempt ask for the same candidate evaluation (as an
    implementer and its judge do); else each submits its own candidate.
    """

    waits: Sequence[bool] = ()
    polls: Sequence[int] = (0,)
    ends: Sequence[Ending] = ("wait",)
    roles: Sequence[str] = ROLES
    submit_only: Sequence[str] = ()
    shares: bool = True
    calls: list[tuple[str, str, str]] = field(default_factory=list)
    """(attempt, role, call id) of every evaluation a turn asked for."""
    _fresh: int = 0
    _jobs: int = 0
    _running: dict[str, int] = field(default_factory=dict)
    _asking: dict[str, str] = field(default_factory=dict)

    # -- the turns' tool calls --------------------------------------------------------------

    def during_turn(
        self, request: core.Request, state: core.CoreState
    ) -> tuple[core.CoreEvent, ...]:
        """The evaluation a fresh turn submits while it runs, as the bridge admits it."""
        del state
        if not isinstance(request, core.DispatchTurn) or request.request_id is None:
            return ()
        role = request.turn.session.role_id.root.removeprefix("toy-")
        if role in self.submit_only:
            return (self._call(request, role),)
        if role in self.roles and self.waits:
            ordinal = self._fresh
            self._fresh += 1
            if self.waits[ordinal % len(self.waits)]:
                self._asking[request.request_id.root] = self.ends[ordinal % len(self.ends)]
                return (self._call(request, role),)
        return ()

    def _call(self, request: core.DispatchTurn, role: str) -> core.AgentMeasurementRequested:
        attempt = request.scope.owner.root
        candidate = revision_of(attempt if self.shares else request.turn.invocation_id.root)
        call = f"call-{len(self.calls)}"
        self.calls.append((attempt, role, call))
        return core.AgentMeasurementRequested(
            scope=request.scope,
            plan=measurement_plan(purpose="local-validation", candidate=candidate),
            within=core.Within(limit=500.0),
            call_id=call,
        )

    # -- the executors ----------------------------------------------------------------------

    def __call__(self, request: core.Request, state: core.CoreState) -> Answer:
        """What the executor of ``request`` reports."""
        match request:
            case core.ObserveOwnedJob():
                return self._observe(request, state)
            case core.SubmitMeasurement():
                return self._submit(request)
            case core.DispatchTurn() | core.ResumeSessionTurn():
                return self._turn(request, state)
            case core.ExecuteRegisteredOperation():
                prompt = core.ArtifactId(root=f"prompt:{request.operation_id.root}")
                return Succeeded(
                    outcome=core.RenderedArtifacts(
                        status="succeeded",
                        prompts=(core.ArtifactRef(artifact_id=prompt, digest="prompt"),),
                    )
                )
            case core.EnsureWorkspace():
                root = f"workspace:{request.scope.owner.root}:{request.attempt.generation}"
                return Succeeded(resource_id=core.ResourceId(root=root), revision=request.plan.base)
            case core.EnsureSession():
                return Succeeded(resource_id=_lease(request.spec.session_id))
            case core.CloseSession():
                return Succeeded(resource_id=_lease(request.session_id))
            case core.SnapshotAndRetain():
                return Succeeded(revision=revision_of(request.attempt.attempt_id.root))
            case core.RetainRevision():
                return Succeeded(revision=request.revision)
            case _:
                return Succeeded()

    def _submit(self, request: core.SubmitMeasurement) -> Answer:
        assert request.request_id is not None
        self._jobs += 1
        job = f"job:{self._jobs}"
        self._running[job] = self.polls[(self._jobs - 1) % len(self.polls)]
        return Running(resource_id=core.ResourceId(root=job))

    def _observe(self, request: core.ObserveOwnedJob, state: core.CoreState) -> Answer:
        job = request.resource_id.root
        left = self._running.get(job, 0)
        if left > 0:
            self._running[job] = left - 1
            return Running(resource_id=request.resource_id)
        owned = next(
            row
            for row in state.evaluation.jobs
            if isinstance(row, core.OwnedJob) and row.resource_id == request.resource_id
        )
        stages = tuple(
            core.EvaluationStageResult(
                stage_id=stage.stage_id, outcome=core.EvaluationStageOutcome.PASSED
            )
            for stage in owned.plan.stages
        )
        plan = owned.plan
        evidence = tuple(
            core.EvidenceRef(
                evidence_id=core.EvidenceId(root=f"{stage.stage_id}:{owned.submission_id.root}"),
                kind=core.EvidenceKind.LOCAL_VALIDATION,
                purpose=plan.purpose,
                scope=owned.scope,
                source_request=owned.submission_id,
                candidate=plan.candidate,
                observation_sequence=0,
                evaluator_digest=plan.evaluator_digest,
                workload_digest=plan.workload_digest,
                environment_digest=plan.environment_digest,
                provenance="trusted",
                status=core.ObservationStatus.SUCCEEDED,
            )
            for stage in plan.stages
        )
        return Succeeded(
            resource_id=request.resource_id,
            evidence=evidence,
            facts=core.EvaluationTerminalFacts(stages=stages, accuracy_passed=True),
        )

    def _turn(
        self, request: core.DispatchTurn | core.ResumeSessionTurn, state: core.CoreState
    ) -> Answer:
        role = request.turn.session.role_id.root.removeprefix("toy-")
        lease = _lease(request.turn.session.session_id)
        if isinstance(request, core.DispatchTurn) and request.request_id is not None:
            ending = self._asking.pop(request.request_id.root, None)
            if ending is not None:
                return self._waiting_turn(request, state, ending, role, lease)
        return Succeeded(output_json=_final(role), resource_id=lease)

    @staticmethod
    def _waiting_turn(
        request: core.DispatchTurn,
        state: core.CoreState,
        ending: Ending,
        role: str,
        lease: core.ResourceId,
    ) -> Answer:
        jobs = tuple(
            job.resource_id
            for job in state.evaluation.jobs
            if isinstance(job, core.OwnedJob) and job.scope == request.scope
        )
        deadlines = [
            call.deadline_at
            for call in state.evaluation.agent_calls
            if call.scope == request.scope and call.deadline_at is not None
        ]
        replies = {
            "wait": waiting_reply([job.root for job in jobs]),
            "result": _final(role),
            "invalid": "not json",
        }
        current = core.InvocationRef(
            session_id=request.turn.session.session_id,
            invocation_id=request.turn.invocation_id,
            generation=request.scope.generation,
        )
        resumed = core.InvocationId(root=f"{current.invocation_id.root}/resume")
        suspension = core.Continuation(
            continuation_id=core.ContinuationId(root=f"{current.invocation_id.root}/evaluation"),
            invocation=current,
            next_invocation=current.model_copy(update={"invocation_id": resumed}),
            jobs=jobs,
            deadline_at=min(deadlines),
            phase=core.ContinuationPhase.WAITING,
        )
        return Succeeded(output_json=replies[ending], resource_id=lease, suspension=suspension)


def run_toy(
    host: ToyHost,
    config: Config | None = None,
    *,
    limits: core.Limits = LIMITS,
    faults: Faults | None = None,
) -> Trace:
    """Drive the toy search on the real core against ``host`` until nothing more happens."""
    chosen = config or Config()
    pipeline = search(chosen)
    harness = Harness(
        registry=OPERATIONS.registry(),
        facts=FACTS,
        limits=limits,
        lifecycle=frozenset({"suspend"}),
        during_turn=host.during_turn,
        shell_events=True,
        schemas={agent.schema: agent.reply for agent in agents()},
        envelope_type=core.RunEnvelope[pipeline.state_model],
    )
    return drive(pipeline, chosen, host, harness, faults)


def summary(trace: Trace) -> Summary:
    """What the search published at the end of the run."""
    inner = getattr(trace.state, "inner", None)
    assert isinstance(inner, core.FlowState), type(trace.state)
    published = core.FlowSearch.published(inner, Summary)
    assert published is not None, "the search published no summary"
    return published
