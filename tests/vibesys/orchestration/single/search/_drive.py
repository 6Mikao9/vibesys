"""A `SingleSearch` run by hand: the test plays core, event by event.

`Drive` hands the search the events a round's phase takes and exposes the answer only through
the public state (`SingleState.parts`, `Round.phase`) and the decisions. The input fails to
measure, which lets the search design and open rounds without a baseline to build.
"""

from __future__ import annotations

from tests.vibesys.orchestration.single.search._refusals import decision_id
from tests.vibesys.orchestration.single.search._views import empty_view
from tests.vibesys.orchestration.single.search.world import (
    FACTS,
    implemented,
    plan_reply,
    single_config,
)

from vibesys.hypothesis import OrchestratorPlan
from vibesys.orchestration.single.models import SingleAgentRoundResponse
from vibesys.orchestration.single.search.api import Round, RoundPrompt, SingleSearch
from vs_core.api import (
    AskAgent,
    AttemptRef,
    ControlId,
    ControlInput,
    Decision,
    DecisionId,
    Event,
    EvidenceId,
    EvidenceKind,
    EvidenceRef,
    Failed,
    FailureReason,
    InvocationId,
    InvocationRef,
    MeasurementFinished,
    MeasurementResult,
    ObservationStatus,
    RequestId,
    Retained,
    RevisionRef,
    SessionId,
    Settle,
    Settlement,
    SettlementId,
    StopRequested,
    TurnFinished,
    Wait,
    Withdraw,
    WorkspaceClosed,
    WorkspaceOpened,
)

PHASES = ("opening", "implementing", "measuring", "interpreting", "settling")
CANDIDATE = RevisionRef.of_git_commit("c" * 40)


class Drive:
    """The search of one single run, events in, state and decisions out."""

    def __init__(self, **options: object) -> None:
        """Start the search; ``options`` are `SingleConfig` fields (two rounds by default)."""
        self.config = single_config(**options)
        self.search = SingleSearch(self.config)
        self.view = empty_view(FACTS)
        self.state, self.decisions = self.search.init(self.config, FACTS)
        self.asked: list[Decision] = list(self.decisions)

    def send(self, event: Event) -> list[Decision]:
        """Deliver ``event``; the decisions it caused are returned and remembered."""
        self.state, self.decisions = self.search.update(self.state, event, self.view)
        self.asked.extend(self.decisions)
        return self.decisions

    def id(self, name: str) -> DecisionId:
        """The newest asked decision named ``name`` (as core would answer it)."""
        for item in reversed(self.asked):
            identity = decision_id(item)
            if identity is not None and identity.root == name:
                return identity
        message = f"{name} was never asked: {[decision_id(item) for item in self.asked]}"
        raise AssertionError(message)

    def fail(
        self, name: str, reason: FailureReason = FailureReason.TIMED_OUT, **extra: object
    ) -> list[Decision]:
        """End the decision ``name`` the way core can: timed out, or as ``reason`` says."""
        fields: dict[str, object] = {"reason": reason, "decision": self.id(name), **extra}
        if reason is FailureReason.TIMED_OUT:
            fields.setdefault("limit_at", 1.0)
        return self.send(Failed.model_validate(fields))

    def stop(self) -> list[Decision]:
        """The operator asks the run to stop."""
        control = ControlInput(control_id=ControlId(root="stop"), action="stop")
        return self.send(StopRequested(control=control))

    @property
    def round(self) -> Round:
        """The one round in flight."""
        found = [part for _, part in self.state.parts.in_order() if isinstance(part, Round)]
        assert len(found) == 1, "exactly one round is open"
        return found[0]

    @property
    def open_rounds(self) -> list[int]:
        """The numbers of the rounds in flight (round 1 ended, a later one may have opened)."""
        return [part.number for _, part in self.state.parts.in_order() if isinstance(part, Round)]

    @property
    def phase(self) -> str:
        """The kind of phase the round is in."""
        return self.round.phase.kind

    # -- getting to a phase ---------------------------------------------------------------

    def open_round(self) -> None:
        """Fail the input measurement, answer the designer: round 1 asks for its attempt."""
        self.fail("baseline/measure.0")
        plan = OrchestratorPlan.model_validate_json(plan_reply())
        self.send(TurnFinished(decision=self.id("design/plan/1.0"), invocation=_turn(), reply=plan))
        assert self.phase == "opening"

    def reach(self, phase: str) -> None:
        """Walk the happy path until the round is in ``phase``."""
        self.open_round()
        steps = [self.opened, self.implement, self.measure, self.decoding_lost]
        for step in steps[: PHASES.index(phase)]:
            step()
        assert self.phase == phase

    def opened(self) -> None:
        """Core opened the workspace."""
        attempt = AttemptRef(attempt_id=self.round.attempt, generation=0)
        self.send(WorkspaceOpened(decision=self.id("round.1/start"), attempt=attempt))

    def implement(self) -> None:
        """The implementer answered with a passing reply and core retained its checkpoint."""
        reply = SingleAgentRoundResponse.model_validate_json(implemented())
        event = TurnFinished(
            decision=self.id("round.1/implementer.0"),
            invocation=_turn(),
            reply=reply,
            checkpoint=Retained(revision=CANDIDATE),
        )
        self.send(event)

    def measure(self) -> None:
        """The measurement ended with trusted evidence of the candidate."""
        scope = self.round.scope
        ref = EvidenceRef(
            evidence_id=EvidenceId(root="benchmark"),
            kind=EvidenceKind.BENCHMARK,
            purpose="official",
            scope=scope,
            source_request=_request(),
            candidate=CANDIDATE,
            observation_sequence=1,
            evaluator_digest=FACTS.evaluator_digest,
            workload_digest=FACTS.workload_digest,
            environment_digest=FACTS.environment_digest,
            provenance="trusted",
            status=ObservationStatus.SUCCEEDED,
        )
        result = MeasurementResult(
            scope=scope,
            evidence=(ref,),
            status=ObservationStatus.SUCCEEDED,
            source_request=_request(),
        )
        self.send(MeasurementFinished(decision=self.id("round.1/measure.0"), result=result))

    def decoding_lost(self) -> None:
        """The decoding timed out, so the attempt settles (an operation result is not hand-built)."""
        self.fail("round.1/interpret")

    def close(self) -> list[Decision]:
        """Core settles the attempt."""
        settlement = Settlement(
            settlement_id=SettlementId(root="settled"),
            attempt=self.round.ref,
            candidate=None,
            assessments=(),
            eligible=False,
            retention="discard",
            outcome="failed",
        )
        return self.send(WorkspaceClosed(decision=self.id("round.1/settle"), settlement=settlement))


def _turn() -> InvocationRef:
    return InvocationRef(
        session_id=SessionId(root="session"), invocation_id=InvocationId(root="turn"), generation=0
    )


def _request() -> RequestId:
    return RequestId(root="request")


def asked_once(decisions: list[Decision], name: str) -> Wait:
    """The wait named ``name``, which ``decisions`` ask exactly once."""
    found = [item for item in decisions if decision_id(item) == DecisionId(root=name)]
    assert len(found) == 1, f"{name} not asked once in {[decision_id(item) for item in decisions]}"
    assert isinstance(found[0], Wait)
    return found[0]


def prompt_of(decisions: list[Decision], name: str) -> RoundPrompt:
    """The round prompt of the implementer ask ``name``."""
    request = asked_once(decisions, name).request
    assert isinstance(request, AskAgent)
    assert isinstance(request.input, RoundPrompt)
    return request.input


def settle_of(decisions: list[Decision], name: str) -> Settle:
    """The settlement a withdrawal ``name`` asserts."""
    request = asked_once(decisions, name).request
    assert isinstance(request, Withdraw)
    assert isinstance(request.disposition, Settle)
    return request.disposition
