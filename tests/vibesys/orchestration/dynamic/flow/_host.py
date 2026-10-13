"""A fake core for the dynamic flow: it answers the waits the flow opens, as a story or by script.

`Host` plays everything outside the flow: the workspace executor (open, settle), the agents (the
planner, the implementer, the judge), the evaluator (measure, interpret), the parent verifier and
adoption. A story answers one wait at a time (`answer`); a generated test chooses which open wait
to answer and how (`step`). Nothing here has a clock or a thread: `FlowRun` is the whole world.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from tests.vibesys.orchestration.dynamic.search._replies import (
    implement,
    implemented,
    plan_reply,
    reviewed,
)
from tests.vibesys.orchestration.dynamic.search._run import FACTS

from vibesys.metrics import EvidenceReading
from vibesys.orchestration.dynamic.flow.api import RunSnapshot, dynamic_search
from vibesys.orchestration.dynamic.models import (
    ImplementerResult,
    ReviewResult,
    planner_reply_type,
)
from vibesys.orchestration.dynamic.search.api import (
    OPERATIONS,
    DynamicConfig,
    EvidenceReadings,
    InterpretEvidence,
    MetricRow,
    ParentVerification,
)
from vs_core.api import (
    AdoptionFinished,
    AdoptionResult,
    AskAgent,
    AttemptRef,
    CheckpointDecline,
    Declined,
    EventId,
    EvidenceId,
    EvidenceKind,
    EvidenceRef,
    Failed,
    FailureReason,
    FlowSearch,
    InvocationId,
    InvocationRef,
    Measure,
    MeasurementFinished,
    MeasurementResult,
    Observation,
    ObservationStatus,
    Operation,
    OperationFinished,
    OperationId,
    OperationResult,
    ProposeWinner,
    RejectionCode,
    ReplyError,
    ReplyErrorCode,
    RequestId,
    Retained,
    RevisionRef,
    SessionId,
    Settle,
    Settlement,
    SettlementId,
    StartAttempt,
    TurnFailureKind,
    Withdraw,
    WorkspaceClosed,
    WorkspaceOpened,
)
from vs_core.api.testing import Asked, FlowRun

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import BaseModel

    from vs_core.api import OperationRequest, Scope

_KINDS = {
    "accuracy": EvidenceKind.CORRECTNESS,
    "benchmark": EvidenceKind.BENCHMARK,
    "profile": EvidenceKind.PROFILING,
}
_REGISTRY = OPERATIONS.registry()


def begin(config: DynamicConfig, *, restarting: bool) -> Host:
    """Start the dynamic search of ``config`` under a host."""
    run = FlowRun.start(dynamic_search(config), config, FACTS, restarting=restarting)
    return Host(run, config)


@dataclass
class Host:
    """The world around a dynamic flow."""

    run: FlowRun[DynamicConfig]
    config: DynamicConfig
    opened: list[AttemptRef] = field(default_factory=list)
    """Every attempt the host admitted, in order."""
    refused_starts: int = 0
    settled: set[str] = field(default_factory=set)
    """The attempts whose workspace the host closed."""
    planner_calls: int = 0
    _hypotheses: int = 0
    _commits: int = 0
    _events: int = 0
    refusals: dict[str, int] = field(default_factory=dict)
    """For a kind of wait (its label), how many times core still refuses it."""
    refusal_code: RejectionCode = RejectionCode.BUDGET
    refused_hypotheses: set[str] = field(default_factory=set)
    """Hypotheses whose workstream core refuses to start."""

    # -- what the flow has done ----------------------------------------------------------------

    @property
    def record(self) -> RunSnapshot | None:
        """What the flow last published."""
        return FlowSearch.published(self.run.state, RunSnapshot)

    def open(self, kind: type, *, label: str | None = None) -> list[Asked]:
        """The open waits for a ``kind`` request."""
        return [
            asked
            for asked in self.run.open
            if isinstance(asked.request, kind) and label in (None, asked.label)
        ]

    @property
    def unsettled(self) -> list[AttemptRef]:
        """Attempts that were opened and not closed."""
        return [item for item in self.opened if item.attempt_id.root not in self.settled]

    # -- answering -----------------------------------------------------------------------------

    def answer(self, asked: Asked, variant: int = 0) -> None:
        """Answer one open wait; ``variant`` chooses among the ways that kind of wait can end."""
        request = asked.request
        if self.refusals.get(asked.label, 0) > 0:
            self.refusals[asked.label] -= 1
            self._refuse(asked)
            return
        match request:
            case AskAgent():
                self._agent(asked, request, variant)
            case StartAttempt():
                self._start(asked, request, variant)
            case Withdraw():
                self._withdraw(asked, request, variant)
            case Measure():
                self._measure(asked, request)
            case Operation():
                self._operation(asked, request)
            case ProposeWinner():
                self._propose(asked, request, variant)
            case _:
                message = f"nothing to answer {type(request).__name__}"
                raise TypeError(message)

    def _refuse(self, asked: Asked) -> None:
        if isinstance(asked.request, StartAttempt):
            self.refused_starts += 1
        self.run.fail(asked, FailureReason.REFUSED, cause=self.refusal_code)

    def refuse_finish(self) -> None:
        """Core refuses the result the flow proposed."""
        proposed = self.run.finished
        assert proposed is not None
        self.run.send(
            Failed(
                reason=FailureReason.REFUSED, decision=proposed.decision_id, cause=self.refusal_code
            )
        )

    def step(self, pick: int, variant: int = 0) -> bool:
        """Answer the ``pick``-th open wait (modulo how many), or move the clock; False if idle."""
        answerable = [item for item in self.run.open if item.until is not None]
        if answerable:
            self.answer(answerable[pick % len(answerable)], variant)
            return True
        if self.run.sleeping_until:
            self.run.advance_to(min(self.run.sleeping_until))
            return True
        return False

    def drain(self, *, limit: int = 500) -> None:
        """Answer everything the ordinary way until the flow waits for nothing more."""
        for _ in range(limit):
            if not self.step(0):
                return
        message = f"the flow did not settle in {limit} steps: {self.run.timeline()}"
        raise AssertionError(message)

    # -- agents --------------------------------------------------------------------------------

    def _agent(self, asked: Asked, request: AskAgent, variant: int) -> None:
        role = asked.label
        if role == "planner":
            self._planner(asked, variant)
        elif role == "implementer":
            self._implementer(asked, variant)
        else:
            self._judge(asked, variant)
        del request

    def _planner(self, asked: Asked, variant: int) -> None:
        self.planner_calls += 1
        match variant % 5:
            case 0 | 1:
                names = []
                for _ in range(self._portfolio_size() if variant % 5 == 0 else 1):
                    self._hypotheses += 1
                    names.append(implement(f"h{self._hypotheses}"))
                reply = planner_reply_type(self.config.profile_offer).model_validate_json(
                    plan_reply(*names)
                )
                self.run.reply(asked, reply)
            case 2:
                error = ReplyError(path=("workstreams",), code=ReplyErrorCode.NOT_JSON)
                self.run.fail(asked, FailureReason.INVALID_REPLY, errors=(error,))
            case 3:
                self.run.fail(asked, FailureReason.TIMED_OUT)
            case _:
                self.run.fail(asked, FailureReason.LOST, turn=self._turn())

    def _portfolio_size(self) -> int:
        """How many workstreams fill the slots free now, within the budget left."""
        record = self.record
        busy = 0 if record is None else len(record.running)
        started = 0 if record is None else busy + len(record.finished)
        left = self.config.start_budget - started + self.refused_starts
        return max(1, min(self.config.max_in_flight - busy, left))

    def _implementer(self, asked: Asked, variant: int) -> None:
        match variant % 6:
            case 0 | 2:
                outcome = "supported" if variant % 6 == 0 else "implementation_failed"
                result = ImplementerResult.model_validate_json(implemented(outcome))
                self.run.reply(asked, result, checkpoint=self._checkpoint())
            case 1:
                result = ImplementerResult.model_validate_json(implemented("blocked"))
                self.run.reply(asked, result)
            case 3:
                self.run.fail(
                    asked, FailureReason.TURN_FAILED, turn_failure=TurnFailureKind.PROVIDER_FAILED
                )
            case 4:
                self.run.fail(asked, FailureReason.TIMED_OUT)
            case _:
                result = ImplementerResult.model_validate_json(implemented())
                declined = Declined(reason=next(iter(CheckpointDecline)))
                self.run.reply(asked, result, checkpoint=declined)

    def _checkpoint(self) -> Retained:
        self._commits += 1
        return Retained(revision=RevisionRef.of_git_commit(f"c{self._commits}"))

    def _judge(self, asked: Asked, variant: int) -> None:
        if variant % 3 == 2:
            self.run.fail(
                asked, FailureReason.TURN_FAILED, turn_failure=TurnFailureKind.PROVIDER_FAILED
            )
            return
        self.run.reply(asked, ReviewResult.model_validate_json(reviewed(passed=variant % 3 == 0)))

    def _turn(self) -> InvocationRef:
        self._events += 1
        return InvocationRef(
            session_id=SessionId(root="session"),
            invocation_id=InvocationId(root=f"lost-{self._events}"),
            generation=0,
        )

    # -- workspace executor ------------------------------------------------------------------------

    def _start(self, asked: Asked, request: StartAttempt, variant: int) -> None:
        work = asked.decision.root.partition("/")[0].partition(".")[2]
        if variant % 4 == 3 or work in self.refused_hypotheses:
            self._refuse(asked)
            return
        attempt = AttemptRef(attempt_id=request.attempt_id, generation=0)
        self.opened.append(attempt)
        self.run.answer(asked, WorkspaceOpened(decision=asked.decision, attempt=attempt))

    def _withdraw(self, asked: Asked, request: Withdraw, variant: int) -> None:
        disposition = request.disposition
        target = request.target
        assert isinstance(disposition, Settle)
        assert isinstance(target, AttemptRef)
        if variant % 4 == 3 and disposition.outcome != "failed":
            self._refuse(asked)  # core takes the plain close instead
            return
        self.settled.add(target.attempt_id.root)
        settlement = Settlement(
            settlement_id=SettlementId(root=f"settlement-{len(self.settled)}"),
            attempt=target,
            candidate=disposition.candidate,
            assessments=disposition.assessments,
            eligible=disposition.eligible,
            retention=disposition.retention,
            outcome=disposition.outcome,
        )
        self.run.answer(asked, WorkspaceClosed(decision=asked.decision, settlement=settlement))

    # -- evaluator and operations -------------------------------------------------------------------

    def _measure(self, asked: Asked, request: Measure) -> None:
        plan = request.plan
        assert isinstance(plan.candidate, RevisionRef)
        candidate = plan.candidate
        refs = tuple(
            EvidenceRef(
                evidence_id=EvidenceId(root=f"{stage.stage_id}:{plan.purpose}:{index}"),
                kind=_KINDS[stage.stage_id],
                purpose=plan.purpose,
                scope=request.scope,
                source_request=RequestId(root=f"measure-{index}"),
                candidate=candidate,
                observation_sequence=0,
                evaluator_digest=plan.evaluator_digest,
                workload_digest=plan.workload_digest,
                environment_digest=plan.environment_digest,
                provenance="trusted",
                status=ObservationStatus.SUCCEEDED,
            )
            for index, stage in enumerate(plan.stages)
        )
        result = MeasurementResult(
            scope=request.scope, evidence=refs, status=ObservationStatus.SUCCEEDED
        )
        self.run.answer(asked, MeasurementFinished(decision=asked.decision, result=result))

    def _operation(self, asked: Asked, request: Operation) -> None:
        typed = request.request
        if isinstance(typed, InterpretEvidence):
            outcome = EvidenceReadings(
                status="succeeded", readings=tuple(_reading_of(ref) for ref in typed.evidence)
            )
        else:
            outcome = ParentVerification(status="succeeded", verified=True)
        self.run.answer(asked, self._finished(asked, typed, outcome, request))

    def _finished(
        self, asked: Asked, typed: OperationRequest, outcome: BaseModel, request: Operation
    ) -> OperationFinished:
        schema = _REGISTRY.encode(typed).schema_ref
        result = OperationResult(
            operation_id=OperationId(root=f"operation-{asked.decision.root}"),
            observation=self._observation(request.scope),
            outcome_schema=schema.outcome_schema,
            operation_schema=schema,
            outcome=outcome,
        )
        return OperationFinished(decision=asked.decision, result=_REGISTRY.validate_event(result))

    def _observation(self, scope: Scope) -> Observation:
        self._events += 1
        return Observation(
            event_id=EventId(root=f"event-{self._events}"),
            request_id=RequestId(root=f"request-{self._events}"),
            scope=scope,
            sequence=1,
            observed_at=self.run.view.now_at,
            status=ObservationStatus.SUCCEEDED,
            terminal=True,
            released=True,
        )

    def _propose(self, asked: Asked, request: ProposeWinner, variant: int) -> None:
        if variant % 2:
            self._refuse(asked)
            return
        result = AdoptionResult(
            selection=request.selection, observation=self._observation(request.scope)
        )
        self.run.answer(asked, AdoptionFinished(decision=asked.decision, result=result))


def _reading_of(ref: EvidenceRef) -> EvidenceReading:
    metrics = ()
    if ref.kind is EvidenceKind.BENCHMARK:
        value = 50.0 if ref.purpose == "baseline" else 100.0
        metrics = (MetricRow(name="throughput", value=value, direction="max", unit="tokens/s"),)
    return EvidenceReading(
        evidence_id=ref.evidence_id,
        kind=ref.kind,
        passed=True,
        stage=ref.kind.value,
        metrics=metrics,
    )


def play(host: Host, steps: Sequence[tuple[int, int]]) -> None:
    """Answer the open waits as ``steps`` say (which wait, which way), then in the ordinary way."""
    for pick, variant in steps:
        if not host.step(pick, variant):
            break
    host.drain()


def assert_flow_closed(host: Host) -> None:
    """The flow finished the run and left no attempt open and no wait unanswered."""
    assert host.run.finished is not None, host.run.timeline()
    assert not host.unsettled, host.run.timeline()
    assert not [item for item in host.run.open if item.until is not None], host.run.timeline()
