"""A stopped host takes its agent sessions with it; the resumed run must not wait on them.

An agent session and its open turn live in the host instance that started them. When that
instance is stopped and killed (the operator's SIGTERM, then the kill after its grace) with the
provider wedged, its turns, cancels and closes are begun and never sealed, and no executor is
left that could say what they did. A resume that asked and then waited for the answer ended the
run blocked ("recovery blocked: outcome unknown for ...") although everything it needed was
already known: the instance that owned those requests is gone.

The world kills the host at every kind of durable point (`Point`) after a stop with the provider
wedged, for any number of durable points of grace, and resumes it on the production resume
policy, shell and recovery over Fake Git and the instance-bound fakes of `vs_dst`, whose
receipts survive the host and whose provider, turns and open sessions do not.
"""

from __future__ import annotations

from collections import Counter
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING

import pytest
from hypothesis import Phase, example, given, settings
from hypothesis import strategies as st
from tests.support.dynamic_script import REGISTRY
from tests.vibesys.orchestration.dynamic._crash_resume import Point, WedgedStopWorld

from vs_core.api import CancelTurn, FlowState, RecoveryPhase, RunStatus
from vs_core.api.testing import Budget, End, liveness
from vs_faults.api import HostCrashError
from vs_project.api import StoredEnvelope
from vs_runtime.api.core import RuntimeRecord
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from vs_core.api import CoreState
    from vs_runtime.api.testing import Run


def resume_after_wedged_stop(
    at: int, delay: int, grace: int, boundary: Point
) -> tuple[CoreState, Run]:
    """The core state the stopped host left, and the run the resumed host made of it."""
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as environment:
        environment.setenv("VIBESYS_STATE_HOME", str(Path(directory) / "state"))
        world = WedgedStopWorld(
            Path(directory) / "project", at=at, boundary=boundary, delay=delay, grace=grace
        )
        with world.open(resume=False):
            first = world.host(resume=False, now=1)
            with suppress(HostCrashError):
                first.run()
            stored = world.store.load()
            assert isinstance(stored, StoredEnvelope)
            before = RuntimeRecord[FlowState].decode(stored, REGISTRY).envelope.core
            first.shell.release_lease(now_at=first.clock.now())
        with world.open(resume=True):
            return before, world.host(resume=True, now=first.clock.now() + 1000).run()


def assert_resumes_to_ready(at: int, delay: int, grace: int, boundary: Point) -> None:
    """The class property: whatever the stopped host left in flight, the resume is ready."""
    before, run = resume_after_wedged_stop(at, delay, grace, boundary)
    core = run.core
    assert run.halted is None, run.halted
    assert core.records.recovery.phase is RecoveryPhase.READY
    assert core.run.status is RunStatus.TERMINAL
    assert core.run.result is not None
    reason = core.run.result.reason or ""
    assert core.run.result.outcome != "blocked", reason
    assert "outcome unknown" not in reason
    assert "blocked" not in {check.resolution for check in core.records.recovery.checks}
    recorded = {item.settlement_id: item for item in core.settlement.settlements}
    assert all(recorded[item.settlement_id] == item for item in before.settlement.settlements)
    limit = core.run.limits.request_retries.limit
    # A lost turn is cancelled once and asked again at most `limit` more times, and the stop
    # cancels it once more: re-asking never goes past the retry limit however often hosts die.
    cancels = Counter(
        record.request.invocation
        for record in core.records.records
        if isinstance(record.request, CancelTurn)
    )
    assert all(count <= limit + 2 for count in cancels.values()), cancels
    assert not liveness(run.journal, core, Budget(retries=limit), End.TERMINAL)


@settings(
    max_examples=examples(pr=6, nightly=80), phases=(Phase.explicit, Phase.generate), database=None
)
@example(at=13, delay=6, grace=3, boundary=Point.COMMIT)
@given(
    at=st.integers(1, 40),
    delay=st.integers(1, 30),
    grace=st.integers(0, 12),
    boundary=st.sampled_from(tuple(Point)),
)
def test_a_run_stopped_with_its_agents_wedged_resumes_to_ready(
    at: int, delay: int, grace: int, boundary: Point
) -> None:
    try:
        assert_resumes_to_ready(at, delay, grace, boundary)
    except AssertionError as error:
        error.add_note(
            f"provider wedged after {boundary} {at}; stopped {delay} points later; "
            f"killed {grace} points after that"
        )
        raise
