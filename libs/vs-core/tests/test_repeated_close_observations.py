"""An executor's repeated release proof cannot erase core's synthetic disposition."""

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import vs_core.api as core

from .test_blocked_close import _SILENT_ANSWERS, _close_after, _original, _record


@pytest.mark.regression
@example(lifecycle=core.LifecycleClass.IDEMPOTENT_WRITE, answered=False, offset=0, repeats=2)
@given(
    lifecycle=st.sampled_from(list(core.LifecycleClass)),
    answered=st.booleans(),
    offset=st.integers(0, 5),
    repeats=st.integers(2, 6),
)
def test_repeated_inspections_of_a_request_ended_during_shutdown_are_inert(
    lifecycle: core.LifecycleClass, *, answered: bool, offset: int, repeats: int
) -> None:
    """A never-begun proof refines release, then the same proof is inspected again.

    SIGTERM's cancelling stop can end an unanswered request at its bound. Core's
    synthetic failure and the executor's refusal share a sequence but have different
    statuses. The second inspection must retain the failure and its release proof,
    as the first inspection does, rather than raise a sequence conflict.
    """
    run = _close_after(
        _record(lifecycle, _SILENT_ANSWERS[0], accepted=answered, named=False, answered=answered),
        restart=False,
        late_by=1.0,
        stop_first=True,
    )
    ended = _original(run).observation
    assert ended is not None
    proof = ended.model_copy(
        update={
            "event_id": core.EventId(root="executor:not-begun"),
            "sequence": ended.sequence + offset,
            "status": core.ObservationStatus.REJECTED,
            "released": True,
            "children_complete": True,
            "diagnostic": "no begun marker: the effect never started",
        }
    )
    for _ in range(repeats):
        run = core.CoreState.model_validate_json(run.model_dump_json())
        run = core.step(run, core.RequestObserved(observation=proof)).state
        stored = _original(run).observation
        assert stored is not None
        assert stored.status == core.ObservationStatus.FAILED
        assert stored.released
        assert stored.children_complete
        assert stored.sequence == proof.sequence
        assert run.run.status in (core.RunStatus.CLOSING, core.RunStatus.TERMINAL)
