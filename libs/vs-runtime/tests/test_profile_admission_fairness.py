"""Independent jobs progress across idle official sequence leases, on the virtual loop."""

from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_evaluation.api import (
    ContentDigest,
    EvaluationRequest,
    EvaluationState,
    EvaluationStep,
    EvidenceFingerprints,
    EvidenceKind,
    SemanticEvaluationStage,
)
from vs_runtime.api import (
    CommandProfileCapture,
    CommandResult,
    EvaluationAdmission,
    PollingEvaluationExecutor,
)
from vs_runtime.api.infrastructure import TrustedEvaluationPlan
from vs_runtime.api.testing import FakeCommands, FakeEvaluation, FakeWorkspace, FakeWorkspaces
from vs_sim.api import DISCARD
from vs_sim.api.testing import ManualClock, Sim, yield_until

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from vs_runtime.api.core import StageRun
    from vs_sim.api import OutputSink


def _request(
    snapshot: str, key: str, kind: EvidenceKind, *, retain: bool = False
) -> EvaluationRequest:
    digest = ContentDigest.sha256(key.encode())
    stage = SemanticEvaluationStage(
        snapshot=snapshot,
        kind=kind,
        fingerprints=EvidenceFingerprints(
            candidate=digest, evaluator=digest, workload=digest, environment=digest
        ),
    )
    return EvaluationRequest(
        key=key,
        stages=(EvaluationStep(name=kind.value, payload=stage.model_dump(mode="json")),),
        admission={"key": snapshot, "retain": retain} if retain else None,
    )


async def _finished(executor: PollingEvaluationExecutor, handle: str) -> None:
    seen = await yield_until(
        lambda: executor.inspect(handle),
        lambda value: (
            value is not None and value.state in {EvaluationState.SUCCEEDED, EvaluationState.FAILED}
        ),
        f"{handle} finishes without waiting for an idle sequence's successor",
    )
    assert seen is not None
    assert seen.state is EvaluationState.SUCCEEDED, seen


@pytest.mark.regression
@example(slots=1, kinds=(EvidenceKind.PROFILE,), queued_while_running=False, schedule_seed=0)
@example(
    slots=3,
    kinds=(EvidenceKind.PROFILE, EvidenceKind.ACCURACY, EvidenceKind.BENCHMARK),
    queued_while_running=True,
    schedule_seed=42,
)
@given(
    slots=st.integers(1, 3),
    kinds=st.lists(
        st.sampled_from((EvidenceKind.PROFILE, EvidenceKind.ACCURACY, EvidenceKind.BENCHMARK)),
        min_size=1,
        max_size=8,
    ).map(tuple),
    queued_while_running=st.booleans(),
    schedule_seed=st.integers(0, 2**32 - 1),
)
def test_idle_sequences_cannot_starve_profiles_or_validations(
    slots: int, kinds: tuple[EvidenceKind, ...], *, queued_while_running: bool, schedule_seed: int
) -> None:
    """Submission FIFO survives generated task schedules and delayed official successors."""
    Sim(seed=schedule_seed, schedule_seed=schedule_seed).run(
        _exercise(slots, kinds, queued_while_running=queued_while_running)
    )


async def _exercise(
    slots: int, kinds: tuple[EvidenceKind, ...], *, queued_while_running: bool
) -> None:
    commands = FakeCommands()
    commands.script(
        *(
            CommandResult(
                output='{"kind":"hello","protocol":2,"metrics":{"prefill_timing":{"unit":"ms"}}}\n'
                '{"kind":"result","values":{"prefill_timing":1}}',
                exit_code=0,
            )
            for kind in kinds
            if kind is EvidenceKind.PROFILE
        )
    )
    capture = CommandProfileCapture(
        commands,
        TrustedEvaluationPlan(profile_command="profile", profile_fields=("prefill_timing",)),
        lambda _workspace: "profile",
    )
    evaluation = FakeEvaluation()
    gates = tuple(evaluation.gate("accuracy", index) for index in range(slots))
    workspaces = FakeWorkspaces(
        FakeWorkspace(path=Path("/workspace")), supports_parallel_candidates=True
    )
    admission = EvaluationAdmission(slots)
    started: list[str] = []

    def record(run: StageRun) -> AbstractContextManager[OutputSink]:
        started.append(run.handle_id)
        return nullcontext(DISCARD)

    executor = PollingEvaluationExecutor(
        evaluation,
        workspaces,
        clock=ManualClock(),
        admission=admission,
        profile_capture=capture,
        stage_output=record,
    )
    snapshots = tuple([await workspaces.root.snapshot(f"official-{i}") for i in range(slots)])
    waiting = tuple([await workspaces.root.snapshot(f"waiting-{i}") for i in range(len(kinds))])
    try:
        for index, snapshot in enumerate(snapshots):
            await executor.submit(
                _request(snapshot, f"official-{index}", EvidenceKind.ACCURACY, retain=True),
                handle_id=f"official-{index}",
            )

        async def all_entered() -> bool:
            return all(gate.entered.is_set() for gate in gates)

        await yield_until(all_entered, bool, "all official slots execute their first members")
        if not queued_while_running:
            for gate in gates:
                gate.release()
            await asyncio.gather(
                *(_finished(executor, f"official-{index}") for index in range(slots))
            )
        for index, (snapshot, kind) in enumerate(zip(waiting, kinds, strict=True)):
            await executor.submit(
                _request(snapshot, f"waiting-{index}", kind), handle_id=f"waiting-{index}"
            )
        if queued_while_running:
            await yield_until(
                admission.counts,
                lambda counts: counts == (slots, len(kinds)),
                "all independent jobs join the FIFO",
            )
            for gate in reversed(gates):
                gate.release()
        await asyncio.gather(
            *(_finished(executor, f"waiting-{index}") for index in range(len(kinds)))
        )
        assert [handle for handle in started if handle.startswith("waiting-")] == [
            f"waiting-{index}" for index in range(len(kinds))
        ]
        # Retained sequence identity still accepts its final member after yielding.
        for index, snapshot in enumerate(snapshots):
            request = _request(snapshot, f"final-{index}", EvidenceKind.BENCHMARK).model_copy(
                update={"admission": {"key": snapshot, "retain": False}}
            )
            await executor.submit(request, handle_id=f"final-{index}")
        await asyncio.gather(*(_finished(executor, f"final-{index}") for index in range(slots)))
        assert await admission.counts() == (0, 0)
        assert len(commands.calls) == kinds.count(EvidenceKind.PROFILE)
    finally:
        await executor.close()
