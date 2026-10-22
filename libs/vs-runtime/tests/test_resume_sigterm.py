"""SIGTERM's cancelling control at evaluation boundaries is resumable on matching code."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from tests.support.dynamic_script import FACTS, Executors
from tests.support.skeleton_strategy import OPERATIONS, SkeletonStrategy, skeleton

import vs_core.api as core
from vs_core.api.testing import Answer, Harness, Running, sample_limits
from vs_dst.api import Effects, RecoverableExecutors
from vs_project.api import FakeStateStore
from vs_runtime.api.testing import LEASE, HostOptions, Launch, scripted_host
from vs_sim.api import Signals
from vs_sim.api.testing import FakeSignalSource

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from pydantic import BaseModel

    from vs_core.api.testing import Journal
    from vs_runtime.api.core import CoreRuntime
    from vs_runtime.api.testing import Script
    from vs_sim.api.testing import VirtualClock


class _EvaluationStops:
    def __init__(self, at: int, running_polls: int) -> None:
        self.signals = FakeSignalSource()
        self.stopped = False
        self._at = at
        self._boundary = 0
        self._polls = running_polls
        self._base = Executors()

    def attach(self, shell: CoreRuntime[core.FlowState], clock: VirtualClock) -> None:
        def terminate() -> None:
            # The server's SIGTERM handler requests this cancelling control; no real
            # process signal is needed to vary the interruption point deterministically.
            shell.submit(
                core.RunControlEvent(
                    control=core.ControlInput(
                        control_id=core.ControlId(root="sigterm"),
                        action="stop",
                        mode=core.StopMode.CANCEL,
                    ),
                    now_at=clock.now(),
                ),
                now_at=clock.now(),
            )

        self.signals.add_handler(Signals.SIGTERM, terminate)

    def __call__(self, request: core.Request, state: core.CoreState) -> Answer:
        if isinstance(request, core.SubmitMeasurement | core.ObserveOwnedJob):
            if not self.stopped and self._boundary == self._at:
                self.stopped = True
                self.signals.deliver(Signals.SIGTERM)
            self._boundary += 1
        if isinstance(request, core.ObserveOwnedJob) and self._polls:
            self._polls -= 1
            return Running(resource_id=request.resource_id)
        return self._base(request, state)


@pytest.mark.regression
@example(stop_at=0, running_polls=0)
@example(stop_at=1, running_polls=0)
@example(stop_at=4, running_polls=3)
@given(stop_at=st.integers(0, 10), running_polls=st.integers(0, 6))
def test_sigterm_at_any_evaluation_boundary_resumes_without_unknown_outcomes(
    stop_at: int, running_polls: int
) -> None:
    """Every submission/poll point, with a variable number of pending observations.

    Uses the production shell/core, a durable Fake store, recoverable executor
    receipts and simulated signals/time. The first host finishes SIGTERM cleanup;
    its successor lifts the stop and must finish with every job released.
    """
    world = _EvaluationStops(stop_at % (running_polls + 2), running_polls)
    store = FakeStateStore()
    effects = Effects()

    def translator(
        script: Script,
        read: Callable[[], core.CoreState],
        registry: core.OperationRegistry,
        schemas: Mapping[core.SchemaRef, type[BaseModel]],
        journal: Journal,
    ) -> RecoverableExecutors:
        return RecoverableExecutors(script, read, registry, schemas, journal, effects)

    launch = Launch(skeleton, SkeletonStrategy.baseline_only().config)
    hosting = Harness(
        registry=OPERATIONS.registry(),
        facts=FACTS,
        limits=sample_limits(observe_interval=1.0, observe_backoff_cap=1.0),
        envelope_type=core.RunEnvelope[core.FlowState],
        schemas=skeleton.reply_schemas,
        shell_events=True,
        deadline_at=15000.0,
    )
    first = scripted_host(
        launch,
        world,
        hosting,
        skeleton.reply_schemas,
        HostOptions(attach=world.attach, translator=translator),
        store=store,
    )
    stopped = first.run()
    assert world.stopped
    assert stopped.halted is None
    assert stopped.core.run.status == core.RunStatus.TERMINAL
    resumed = scripted_host(
        launch,
        world,
        hosting,
        skeleton.reply_schemas,
        HostOptions(attach=world.attach, translator=translator, resumed=True),
        store=store,
        clock_at=first.clock.now() + LEASE + 1,
    ).run()
    assert resumed.halted is None
    assert resumed.core.run.status == core.RunStatus.TERMINAL
    assert resumed.core.run.result is not None
    assert resumed.core.run.result.outcome != "blocked"
    assert resumed.core.records.recovery.phase == core.RecoveryPhase.READY
    assert all(job.released for job in resumed.core.evaluation.jobs)
