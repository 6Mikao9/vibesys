"""Input baseline: one trusted measurement of the starting revision, as a small machine.

Candidates and adoption are gated on this reading. The machine measures the input, reads the
evidence back through the search's own interpretation operation, and ends in one of three
phases: ``Measured``, ``Unmeasurable`` (a typed failure the planner is shown) or
``NotConfigured`` (no gate to measure). A measurement that yields no evidence is retried up to
``max_input_measurement_attempts``; a workload rejection of the input is a typed failure, not
an exception.

A search embeds the machine as a child: ``begin`` returns the first phase and decisions,
``update`` takes one event at a time. The phase says what is awaited; nothing else is kept.
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import Field, model_validator

from vibesys.candidates.config import CandidateConfig
from vibesys.metrics import (
    EvidenceReading,
    MetricRow,
    PartialRow,
    accept_readings,
    reading_of,
)
from vs_core.api import (
    THIS_RUN,
    Decision,
    DecisionId,
    Event,
    EvidenceKind,
    EvidenceReadingsBase,
    EvidenceRef,
    Failed,
    FailureReason,
    Measure,
    MeasurementFinished,
    MeasurementRecipe,
    ObservationStatus,
    Operation,
    OperationFinished,
    OperationRequest,
    RejectionCode,
    RunFacts,
    RunView,
    UnexpectedEvent,
    Value,
    Wait,
    Within,
    may_resubmit,
    trusted_keys,
)

_INTERPRET = DecisionId(root="interpret")
_NO_CLOCK = 0.0
"""A run starts at 0 on its own clock, and ``begin`` is called before any event has a time."""


class InputFailureKind(StrEnum):
    """Why the input has no trusted reading. Closed: nothing here is free text."""

    NO_EVIDENCE = "no-evidence"
    """The measurement ended without trusted evidence of the input; ``status`` says how."""

    READINGS_REFUSED = "readings-refused"
    """The decoded readings did not match the evidence that was asked about."""

    READINGS_UNDECODABLE = "readings-undecodable"
    """The evidence owner could not decode the evidence."""

    BENCHMARK_FAILED = "benchmark-failed"
    """The input's own benchmark failed; ``output`` is what the evaluation printed."""

    WAIT_ENDED = "wait-ended"
    """Core ended the measurement or its decoding without a result; ``ended`` says why."""


class InputFailure(Value):
    """A typed reason the input is unmeasurable, with only the fields its kind has.

    ``output`` is text an evaluator printed (a benchmark's feedback, a failed job's diagnostic),
    carried as captured data for the planner prompt and never written by the search.
    """

    kind: InputFailureKind
    status: ObservationStatus | None = None
    output: str = ""
    ended: FailureReason | None = None
    cause: RejectionCode | None = None

    @model_validator(mode="after")
    def fields_match_kind(self) -> "InputFailure":
        """Refuse a field that does not belong to the kind, or a kind missing its field."""
        wanted = {
            "status": self.kind is InputFailureKind.NO_EVIDENCE,
            "ended": self.kind is InputFailureKind.WAIT_ENDED,
        }
        for name, allowed in wanted.items():
            present = getattr(self, name) is not None
            if present != allowed:
                message = f"{name} {'does not belong to' if present else 'is required by'} {self.kind.value}"
                raise ValueError(message)
        if self.cause is not None and self.ended is not FailureReason.REFUSED:
            message = "cause belongs to an ended wait that was refused"
            raise ValueError(message)
        return self


class InputReading(Value):
    """What the trusted evaluations said about the input revision."""

    accuracy_passed: bool | None = None
    benchmark_passed: bool | None = None
    metrics: tuple[MetricRow, ...] = ()
    partial: PartialRow | None = None


class Measuring(Value):
    """The input measurement is running; ``attempts`` counts the submissions so far."""

    kind: Literal["measuring"] = "measuring"
    attempts: int = Field(ge=1)


class Interpreting(Value):
    """The input's trusted evidence is being decoded."""

    kind: Literal["interpreting"] = "interpreting"
    attempts: int = Field(ge=1)
    evidence: tuple[EvidenceRef, ...] = Field(min_length=1)


class Measured(Value):
    """The input has a trusted reading."""

    kind: Literal["measured"] = "measured"
    attempts: int = Field(ge=1)
    reading: InputReading


class Unmeasurable(Value):
    """The input has no trusted reading; candidates then compete on their own readings."""

    kind: Literal["unmeasurable"] = "unmeasurable"
    attempts: int = Field(ge=0)
    failure: InputFailure
    reading: InputReading | None = None


class NotConfigured(Value):
    """No gate is configured, so there is nothing to measure."""

    kind: Literal["not_configured"] = "not_configured"


type Baseline = Annotated[
    Measuring | Interpreting | Measured | Unmeasurable | NotConfigured, Field(discriminator="kind")
]


def stages(config: CandidateConfig) -> tuple[str, ...]:
    """Measurement stages the run configured, accuracy first."""
    return (
        *(("accuracy",) if config.accuracy_configured else ()),
        *(("benchmark",) if config.benchmark_configured else ()),
    )


@dataclass(frozen=True)
class InputMeasurement:
    """What a search supplies to measure its input: the recipe and its own operation."""

    recipe: MeasurementRecipe
    interpret: Callable[[tuple[EvidenceRef, ...]], OperationRequest]
    """Builds the search's operation request that decodes the recorded evidence."""
    operation_seconds: float


def resolved(baseline: Baseline) -> bool:
    """Whether the input measurement has reached a terminal phase."""
    return isinstance(baseline, Measured | Unmeasurable | NotConfigured)


def trusted(baseline: Baseline) -> bool:
    """Whether the input revision has a trusted reading, or no gate is configured to give one."""
    match baseline:
        case Measured(reading=reading):
            return reading.accuracy_passed is not False
        case NotConfigured():
            return True
        case Measuring() | Interpreting() | Unmeasurable():
            return False


def reading(baseline: Baseline) -> InputReading | None:
    """The input's reading, when there is one (an unmeasurable input may keep a partial one)."""
    match baseline:
        case Measured(reading=found):
            return found
        case Unmeasurable(reading=found):
            return found
        case Measuring() | Interpreting() | NotConfigured():
            return None


def failure(baseline: Baseline) -> InputFailure | None:
    """Why the input is unmeasurable, or None."""
    return baseline.failure if isinstance(baseline, Unmeasurable) else None


def _measure(
    index: int, facts: RunFacts, config: CandidateConfig, measuring: InputMeasurement, now_at: float
) -> Decision:
    names = stages(config)
    plan = measuring.recipe.plan(
        facts.baseline,
        purpose="baseline",
        now_at=now_at,
        stages=names,
        submission_limit=config.max_input_measurement_attempts,
    )
    return Wait(
        request=Measure(decision_id=DecisionId(root=f"measure.{index}"), scope=THIS_RUN, plan=plan),
        until=Within(limit=measuring.recipe.budget(names)),
    )


def begin(
    config: CandidateConfig, facts: RunFacts, measuring: InputMeasurement
) -> tuple[Baseline, list[Decision]]:
    """Measure the input, or note that no gate is configured."""
    if not stages(config):
        return NotConfigured(), []
    return Measuring(attempts=1), [_measure(0, facts, config, measuring, _NO_CLOCK)]


def update(  # LW-220406 [PLR0911]; one exhaustive match over (phase, event) is the machine; each arm returns its next phase.
    baseline: Baseline,
    event: Event,
    view: RunView,
    config: CandidateConfig,
    measuring: InputMeasurement,
) -> tuple[Baseline, list[Decision]]:
    """One event: measured, decoded, retried or given up."""
    match baseline, event:
        case Measuring(attempts=submitted), MeasurementFinished(result=result):
            evidence = trusted_keys(
                result, scope=result.scope, candidate=view.facts.baseline, purpose="baseline"
            )
            if may_resubmit(
                result.failure,
                submissions=submitted,
                limit=config.max_input_measurement_attempts,
            ):
                return _again(baseline, view, config, measuring)
            if evidence:
                refs = tuple(item for item in result.evidence if item.key in evidence)
                return Interpreting(attempts=submitted, evidence=refs), [
                    _interpret(refs, measuring)
                ]
            # Core accepts another submission only after a failure `may_resubmit` allows;
            # anything else is final, and asking again would be refused.
            none = InputFailure(
                kind=InputFailureKind.NO_EVIDENCE,
                status=result.status,
                output=result.diagnostic,
            )
            return Unmeasurable(attempts=submitted, failure=none), []
        case Interpreting(attempts=submitted, evidence=refs), OperationFinished(result=result):
            outcome = result.outcome
            if not isinstance(outcome, EvidenceReadingsBase):
                undecoded = InputFailure(kind=InputFailureKind.READINGS_UNDECODABLE)
                return Unmeasurable(attempts=submitted, failure=undecoded), []
            return _read(submitted, refs, outcome.status, tuple(outcome.readings)), []
        case Measuring(attempts=submitted) | Interpreting(attempts=submitted), Failed():
            ended = InputFailure(
                kind=InputFailureKind.WAIT_ENDED, ended=event.reason, cause=event.cause
            )
            return Unmeasurable(attempts=submitted, failure=ended), []
        case _:
            raise UnexpectedEvent(baseline, event)


def _again(
    baseline: Measuring, view: RunView, config: CandidateConfig, measuring: InputMeasurement
) -> tuple[Baseline, list[Decision]]:
    index = baseline.attempts
    submit = _measure(index, view.facts, config, measuring, view.now_at)
    return Measuring(attempts=index + 1), [submit]


def _interpret(refs: tuple[EvidenceRef, ...], measuring: InputMeasurement) -> Decision:
    request = Operation(decision_id=_INTERPRET, scope=THIS_RUN, request=measuring.interpret(refs))
    return Wait(request=request, until=Within(limit=measuring.operation_seconds))


def _read(
    submitted: int,
    refs: tuple[EvidenceRef, ...],
    status: str,
    readings: tuple[EvidenceReading, ...],
) -> Baseline:
    """Fold decoded readings into the baseline; a failed benchmark makes it unmeasurable."""
    accepted = accept_readings(readings, refs)
    if isinstance(accepted, str):
        refused = InputFailure(kind=InputFailureKind.READINGS_REFUSED)
        return Unmeasurable(attempts=submitted, failure=refused)
    accuracy = reading_of(accepted, EvidenceKind.CORRECTNESS)
    benchmark = reading_of(accepted, EvidenceKind.BENCHMARK)
    seen = InputReading(
        accuracy_passed=None if accuracy is None else accuracy.passed,
        benchmark_passed=None if benchmark is None else benchmark.passed,
        metrics=() if benchmark is None else benchmark.metrics,
        partial=None if benchmark is None else benchmark.partial,
    )
    if status != "succeeded" or not accepted:
        undecoded = InputFailure(kind=InputFailureKind.READINGS_UNDECODABLE)
        return Unmeasurable(attempts=submitted, failure=undecoded, reading=seen)
    if benchmark is not None and not benchmark.passed:
        failed = InputFailure(kind=InputFailureKind.BENCHMARK_FAILED, output=benchmark.feedback)
        return Unmeasurable(attempts=submitted, failure=failed, reading=seen)
    return Measured(attempts=submitted, reading=seen)
