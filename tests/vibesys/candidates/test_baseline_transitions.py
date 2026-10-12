"""The input measurement's phases and the events each phase takes (a transition table)."""

from __future__ import annotations

import pytest
from tests.vibesys.orchestration.dynamic.search._views import empty_view, evidence_ref, revision

from vibesys.candidates.api import (
    Baseline,
    CandidateConfig,
    InputFailure,
    InputFailureKind,
    InputReading,
    Interpreting,
    Measured,
    Measuring,
    NotConfigured,
    Unmeasurable,
    resolved,
    trusted,
    update,
)
from vs_core.api import (
    ControlId,
    ControlInput,
    Decision,
    Event,
    EvidenceKind,
    Failed,
    FailureReason,
    ObservationStatus,
    StopRequested,
    UnexpectedEvent,
)

_EVIDENCE = (evidence_ref("e", EvidenceKind.BENCHMARK, revision("input")),)
_ENDED = Failed(reason=FailureReason.TIMED_OUT, limit_at=1.0)
_STOP = StopRequested(control=ControlInput(control_id=ControlId(root="stop"), action="stop"))
_FAILURE = InputFailure(kind=InputFailureKind.NO_EVIDENCE, status=ObservationStatus.FAILED)

SETTLED = (
    NotConfigured(),
    Measured(attempts=1, reading=InputReading()),
    Unmeasurable(attempts=1, failure=_FAILURE),
)
OPEN = (Measuring(attempts=1), Interpreting(attempts=1, evidence=_EVIDENCE))


def _step(baseline: Baseline, event: Event) -> tuple[Baseline, list[Decision]]:
    # Only the arms that take no measurement are exercised, so none is supplied.
    return update(baseline, event, empty_view(), CandidateConfig(), None)  # ty: ignore[invalid-argument-type]


@pytest.mark.parametrize("baseline", SETTLED, ids=lambda item: item.kind)
def test_a_settled_input_is_never_told_of_a_second_ending_of_its_wait(baseline: Baseline) -> None:
    """Core tells a decision once that it ended, so a settled input has no arm for it."""
    with pytest.raises(UnexpectedEvent):
        _step(baseline, _ENDED)


@pytest.mark.parametrize("baseline", OPEN, ids=lambda item: item.kind)
def test_an_open_measurement_whose_wait_ends_is_unmeasurable_and_final(
    baseline: Measuring | Interpreting,
) -> None:
    after, decisions = _step(baseline, _ENDED)

    assert isinstance(after, Unmeasurable)
    assert after.failure.kind == InputFailureKind.WAIT_ENDED
    assert after.failure.ended == FailureReason.TIMED_OUT
    assert after.attempts == baseline.attempts
    assert decisions == []
    assert resolved(after)
    assert not trusted(after)


@pytest.mark.parametrize("baseline", [*SETTLED, *OPEN], ids=lambda item: item.kind)
def test_an_event_the_input_never_waits_for_is_refused_in_every_phase(baseline: Baseline) -> None:
    with pytest.raises(UnexpectedEvent):
        _step(baseline, _STOP)
