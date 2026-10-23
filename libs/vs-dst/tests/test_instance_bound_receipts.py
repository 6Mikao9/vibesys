"""An executor that is the host instance leaves only receipts; a result it could not give is no receipt.

The fakes answer a restarted host's inspection as the production store does: a final result was
sealed and is replayed, a request begun and never sealed may have run (Unknown, and core may send
it again to resume it), and one never begun held nothing. For an agent session and its turns (the
host instance is their executor) a result that was not final, such as a turn the provider never
answered, is never sealed: what the instance held died with it. Every other executor's effect is
durable, so what it answered is what a restart finds.
"""

from __future__ import annotations

import pytest

import vs_core.api as core
from vs_core.api.testing import Answer, Journal, Retryable, Succeeded, Unknown
from vs_dst.api import Effects, RecoverableExecutors
from vs_runtime.api.executor_contexts import context_for
from vs_sim.api.testing import VirtualClock, run_virtual

RUN = core.Scope(owner=core.initial_state().run.run_id, generation=0)
SESSION = core.SessionId(root="session:orchestrator:plan")


def _close() -> core.CloseSession:
    return core.CloseSession(request_id=core.RequestId(root="close"), scope=RUN, session_id=SESSION)


def _cancel_job() -> core.CancelOwnedJob:
    return core.CancelOwnedJob(
        request_id=core.RequestId(root="cancel"), scope=RUN, resource_id=core.ResourceId(root="job")
    )


def _inspection_of(request: core.Request) -> core.InspectRequest:
    assert request.request_id is not None
    return core.InspectRequest(
        request_id=core.RequestId(root=f"inspect:{request.request_id.root}"),
        scope=request.scope,
        target=request.request_id,
    )


def _inspected(answer: Answer, request: core.Request) -> core.TargetObservation:
    """What a restarted host's executors say of ``request`` that a host executed and answered."""
    effects = Effects()

    def script(_request: core.Request, _state: core.CoreState) -> Answer:
        return answer

    def host() -> RecoverableExecutors:
        return RecoverableExecutors(
            script, core.initial_state, core.OperationRegistry(()), {}, Journal(), effects
        )

    async def run() -> core.TargetObservation:
        await host().execute(request, context_for(request))
        # the host dies; its successor's executors share only the receipts
        inspected = await host().execute(_inspection_of(request), context_for(request, epoch=2))
        target = inspected.observation.target
        assert target is not None
        return target

    return run_virtual(VirtualClock(1.0), run())


@pytest.mark.parametrize("answer", [Unknown(), Retryable()])
def test_a_result_the_session_executor_could_not_give_is_begun_and_never_sealed(
    answer: Answer,
) -> None:
    found = _inspected(answer, _close())
    assert found.observation.status == core.ObservationStatus.UNKNOWN
    assert found.reissue == core.ReissueProof.BEGUN_UNSEALED


def test_a_final_result_of_the_session_executor_is_sealed_and_found() -> None:
    found = _inspected(Succeeded(resource_id=core.ResourceId(root="lease")), _close())
    assert found.observation.status == core.ObservationStatus.SUCCEEDED
    assert found.reissue is None


@pytest.mark.parametrize("answer", [Unknown(), Succeeded()])
def test_what_a_durable_executor_answered_is_what_a_restart_finds(answer: Answer) -> None:
    found = _inspected(answer, _cancel_job())
    assert found.reissue is None
    assert found.observation.status == (
        core.ObservationStatus.UNKNOWN
        if isinstance(answer, Unknown)
        else core.ObservationStatus.SUCCEEDED
    )
