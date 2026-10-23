"""An agent call that fails for infrastructure always reaches a next step, and a resume honours the width.

Both run through the production resume resolution and shell (`ResumeWorld`), with the fault
injected at generated durable points:

- A provider outage swallows agent turns and hangs the first cancellation of a lost one. Every
  ask (planner, implementer, judge) is asked again as its role says and the run ends in a typed
  terminal state; before, the cancellation's hang left the planner call waiting for ever
  (`OrphanWaitError: session session:orchestrator:plan.1`).
- A resume that plans a narrower admission limit than the attempts the run already holds: none
  of them is lost, their settlements stand, and the run ends under the narrower limit.
"""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import Phase, example, given, settings
from hypothesis import strategies as st
from tests.support.dynamic_script import REGISTRY
from tests.vibesys.orchestration.dynamic._crash_resume import (
    PROVIDER_OUTAGE,
    Fault,
    NarrowedWorld,
    Point,
    ResumeWorld,
    WideWorld,
)

from vs_core.api import FlowState, RecoveryPhase, RunStatus
from vs_faults.api import HostCrashError
from vs_project.api import StoredEnvelope
from vs_runtime.api.core import RuntimeRecord
from vs_sim.api.testing import examples


@pytest.mark.regression
@settings(
    max_examples=examples(pr=4, nightly=30), phases=(Phase.explicit, Phase.generate), database=None
)
@example(at=1, boundary=Point.COMMIT, width=1, extra=0)
@example(at=12, boundary=Point.RECEIPT, width=2, extra=1)
@given(
    at=st.integers(1, 70),
    boundary=st.sampled_from(tuple(Point)),
    width=st.integers(1, 2),
    extra=st.integers(0, 1),
)
def test_a_provider_outage_on_agent_calls_ends_the_run_in_a_typed_terminal_state(
    at: int, boundary: Point, width: int, extra: int
) -> None:
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as environment:
        environment.setenv("VIBESYS_STATE_HOME", str(Path(directory) / "state"))
        world = (WideWorld if width == 2 else ResumeWorld)(
            Path(directory) / "project",
            extra=extra,
            fault=Fault.PROVIDER,
            at=at,
            boundary=boundary,
        )
        with world.open(resume=False):
            run = world.host(resume=False, now=1).run()
        assert run.halted is None, run.halted
        assert run.core.run.status is RunStatus.TERMINAL
        assert run.core.records.recovery.phase is RecoveryPhase.READY
        assert run.core.run.result is not None
        assert run.core.run.result.outcome != "blocked", run.core.run.result.reason
        assert world.outages <= PROVIDER_OUTAGE
        # an outage within every role's asks again costs no settled result
        assert all(item.outcome == "succeeded" for item in run.core.settlement.settlements)


@pytest.mark.regression
@settings(
    max_examples=examples(pr=3, nightly=20), phases=(Phase.explicit, Phase.generate), database=None
)
@example(at=30, extra=1)
@example(at=45, extra=0)
@given(at=st.integers(1, 90), extra=st.integers(0, 1))
def test_a_resume_under_a_narrower_width_keeps_every_admitted_attempt_and_ends_under_it(
    at: int, extra: int
) -> None:
    """The stored run admitted two attempts at once; the resume plans one (``max_parallel`` 1)."""
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as environment:
        environment.setenv("VIBESYS_STATE_HOME", str(Path(directory) / "state"))
        world = NarrowedWorld(Path(directory) / "project", extra=extra, fault=Fault.CRASH, at=at)
        with world.open(resume=False):
            first = world.host(resume=False, now=1)
            with suppress(HostCrashError):
                first.run()
            stored = world.store.load()
            assert isinstance(stored, StoredEnvelope)
            before = RuntimeRecord[FlowState].decode(stored, REGISTRY).envelope.core
            first.shell.release_lease(now_at=first.clock.now())
        with world.open(resume=True):
            run = world.host(resume=True, now=first.clock.now() + 1000).run()
        assert run.halted is None, run.halted
        assert run.core.run.status is RunStatus.TERMINAL
        assert run.core.records.recovery.phase is RecoveryPhase.READY
        assert run.core.run.limits.max_parallel == 1
        recorded = {item.settlement_id: item for item in run.core.settlement.settlements}
        assert all(recorded[item.settlement_id] == item for item in before.settlement.settlements)
        assert {row.attempt_id for row in before.attempts.attempts} <= {
            row.attempt_id for row in run.core.attempts.attempts
        }
