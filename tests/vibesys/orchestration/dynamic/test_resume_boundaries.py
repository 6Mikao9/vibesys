"""Dynamic resume from production manifest policy through the production shell."""

from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from hypothesis import Phase, example, given, settings
from hypothesis import strategies as st
from tests.support.dynamic_script import REGISTRY
from tests.vibesys.orchestration.dynamic._crash_resume import Fault, Point, ResumeWorld

from vs_core.api import ChargeKind, FlowState, RecoveryPhase, RunStatus
from vs_faults.api import HostCrashError
from vs_project.api import StoredEnvelope
from vs_runtime.api.core import RuntimeRecord
from vs_sim.api.testing import examples, swept


@pytest.mark.parametrize("boundary", tuple(Point))
@pytest.mark.regression
@settings(
    max_examples=examples(pr=3, nightly=20), phases=(Phase.explicit, Phase.generate), database=None
)
@example(at=5, fault=Fault.STALL, extra=0)
@example(at=18, fault=Fault.CRASH, extra=1)
@example(at=45, fault=Fault.ROLLBACK, extra=0)
@example(at=60, fault=Fault.INFRASTRUCTURE, extra=0)
@example(at=25, fault=Fault.SIGTERM, extra=1)
@given(at=st.integers(1, 70), fault=st.sampled_from(tuple(Fault)), extra=st.integers(0, 1))
def test_dynamic_resume_resolves_descriptor_git_and_core_budget_together(
    boundary: Point, at: int, fault: Fault, extra: int
) -> None:
    _check_resume(boundary, at, fault, extra)


def _check_resume(boundary: Point, at: int, fault: Fault, extra: int) -> None:
    try:
        _resume_and_assert(boundary, at, fault, extra)
    except AssertionError as error:
        error.add_note(f"{fault} after {boundary} {at}; {extra=}")
        raise


def _resume_and_assert(boundary: Point, at: int, fault: Fault, extra: int) -> None:
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as environment:
        environment.setenv("VIBESYS_STATE_HOME", str(Path(directory) / "state"))
        world = ResumeWorld(
            Path(directory) / "project",
            extra=extra,
            fault=fault,
            at=at,
            boundary=boundary,
        )
        with world.open(resume=False) as resources:
            first = world.host(resume=False, now=1)
            with suppress(HostCrashError):
                first.run()
            stored = world.store.load()
            assert isinstance(stored, StoredEnvelope)
            before = RuntimeRecord[FlowState].decode(stored, REGISTRY).envelope.core
            if fault is Fault.ROLLBACK:
                for key in resources.leftover_candidates():
                    path = resources.project.state.candidate_worktree_directory(
                        resources.state.run_id, key
                    )
                    backup = Path(directory) / key
                    shutil.move(path, backup)
                    resources.git.remove_worktree(path)
                    shutil.move(backup, path)
            first.shell.release_lease(now_at=first.clock.now())
        with world.open(resume=True) as resources:
            for key in resources.leftover_candidates():
                path = resources.project.state.candidate_worktree_directory(
                    resources.state.run_id, key
                )
                assert resources.git.is_linked_worktree(path)
            host = world.host(resume=True, now=first.clock.now() + 1000)
            run = host.run()
            assert run.halted is None, run.halted
            assert run.core.run.status is RunStatus.TERMINAL
            assert run.core.records.recovery.phase is RecoveryPhase.READY
            assert run.core.run.result is not None
            assert run.core.run.result.outcome != "blocked", run.core.run.result.reason
            assert run.core.run.limits.max_attempts == 1 + extra
            if before.run.status is not RunStatus.TERMINAL:
                assert len(run.core.attempts.attempts) == 1 + extra
            recorded = {item.settlement_id: item for item in run.core.settlement.settlements}
            assert all(
                recorded[item.settlement_id] == item for item in before.settlement.settlements
            )
            assert run.core.run.limits.max_attempts >= before.run.limits.max_attempts
            manifest = resources.project.state.load_run(resources.state.run_id)
            assert manifest.orchestration.options["max_rounds"] == 1 + extra
            assert not resources.leftover_candidates()
            if world.refusals:
                assert all(item.outcome == "succeeded" for item in run.core.settlement.settlements)
                assert sum(
                    charge.charged
                    for attempt in run.core.attempts.attempts
                    for charge in attempt.charges
                    if charge.kind is ChargeKind.TURN
                ) == 2 * len(run.core.attempts.attempts)


@pytest.mark.parametrize("fault", tuple(Fault))
@pytest.mark.parametrize("boundary", tuple(Point))
def test_each_durable_point_resumes(boundary: Point, fault: Fault) -> None:
    """Nightly sweeps every commit and sealed journal point; PR samples each kind."""
    with TemporaryDirectory() as directory, pytest.MonkeyPatch.context() as environment:
        environment.setenv("VIBESYS_STATE_HOME", str(Path(directory) / "state"))
        world = ResumeWorld(Path(directory) / "project", extra=0, fault=Fault.STALL, at=0)
        with world.open(resume=False):
            normal = world.host(resume=False, now=1).run()
            assert normal.halted is None
        count = world.store.commits if boundary is Point.COMMIT else world.store.receipts
    for index in swept(count, 1):
        _check_resume(boundary, index + 1, fault, 0)
