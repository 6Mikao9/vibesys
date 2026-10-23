"""The backend's own retries of a measurement whose failure is not the candidate's.

A node fault, a lost job or an ambiguous failure says nothing about the candidate. The
backend measures such a candidate again, within the bounds it declares
(`EvaluationRetries`), and the search never sees the failed tries: it gets one result,
the last. A candidate's own failure is a result and is never measured again. Core
keeps only a hard cap (`MAX_SUBMISSIONS_PER_MEASUREMENT`) that no declaration can lift.

A host's refusal is neither a failure nor a result. When the task's own evaluator declares
that its host refuses the measurement now (`EvidenceFailureKind.ENVIRONMENT_REFUSED`: no
free disk, a device or a lock it admits work under), the measurement waits: it is reported
as queued, with the refusal as its pending reason, and runs again after a backoff, for as
long as the host refuses. A refused try spends none of the tries above, so a host that
refuses a hundred times and then takes the measurement yields the candidate's own result.
Core sees a queued job and credits the time as queue time (`vs_core._wait_clock`): the
measurement's limit does not run, nothing is failed, and only the run's queue budget, its one
hang guard for a job that never starts, or a stop ends the wait.

`RetryingEvaluationExecutor` wraps any `PollingEvaluationExecutor`. Try ``n`` of a
measurement runs under the inner handle `attempt_handle(handle_id, n)`; the wrapper
records the request and the current try durably (an `EvaluationStateNamespace`), so a
restarted host resumes the retrying instead of reporting the failure it was about to
retry.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Final, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

from vs_core.api import MAX_SUBMISSIONS_PER_MEASUREMENT, ContractValidationError, MeasurementFailure
from vs_evaluation.api import (
    EvaluationRequest,
    EvaluationState,
    EvidenceFailureKind,
    EvidenceOutcome,
    ExecutorObservation,
    ExecutorPoll,
    ExecutorRejectedError,
    PollingEvaluationExecutor,
    PollPhase,
    TrustedEvidence,
    same_work,
)
from vs_runtime._failure_classification import observed_failure

if TYPE_CHECKING:
    from contextlib import AbstractContextManager

    from vs_evaluation.api import (
        AvailabilitySnapshot,
        EvaluationStateNamespace,
        ResourceRequirements,
    )
    from vs_runtime._measurement_progress import StageOutputOpener, StageRun
    from vs_sim.api import Clock, OutputSink


_MAX_DOUBLINGS: Final = 62
"""Doublings past which a backoff is at its cap for any declaration; keeps the power finite."""

ENVIRONMENT_WAIT_PREFIX: Final = "waiting on environment: "
"""What a queued measurement's pending reason starts with while its host refuses it."""

_MAX_REASON_CHARS: Final = 300


class EvaluationRetries(BaseModel):
    """How often the backend measures one candidate in all, by why the earlier try failed.

    Both bounds count tries including the first, and neither may exceed core's hard cap.
    No default: the backend's configuration states them.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    infrastructure_attempts: int = Field(ge=1, le=MAX_SUBMISSIONS_PER_MEASUREMENT)
    """Tries in all when a try fails for a reason in the machinery (node fault, lost job)."""
    ambiguous_attempts: int = Field(ge=1, le=MAX_SUBMISSIONS_PER_MEASUREMENT)
    """Tries in all when a try fails and the evidence cannot say whose fault it is."""
    environment_backoff: float = Field(gt=0, allow_inf_nan=False)
    """Seconds before the first run again of a measurement its host refused; each wait doubles."""
    environment_backoff_cap: float = Field(gt=0, allow_inf_nan=False)
    """The longest wait between two runs of a measurement its host refused. The host's refusals
    are not counted: this only bounds how long the backend takes to notice the host recovered."""

    @model_validator(mode="after")
    def ambiguity_is_not_retried_harder(self) -> EvaluationRetries:
        """Reject retrying an ambiguous failure more than a proven machinery failure."""
        if self.ambiguous_attempts > self.infrastructure_attempts:
            raise ContractValidationError(
                "ambiguous_attempts", "must not exceed infrastructure_attempts"
            )
        if self.environment_backoff_cap < self.environment_backoff:
            raise ContractValidationError(
                "environment_backoff_cap", "must be at least environment_backoff"
            )
        return self

    def environment_delay(self, refusals: int) -> float:
        """Seconds to wait after the host's ``refusals``-th refusal before running again."""
        doublings = min(max(refusals - 1, 0), _MAX_DOUBLINGS)
        return min(self.environment_backoff * 2**doublings, self.environment_backoff_cap)

    def allows_another(self, failure: MeasurementFailure | None, *, tries: int) -> bool:
        """Whether a measurement that failed as ``failure`` after ``tries`` tries is tried again."""
        if failure is MeasurementFailure.INFRASTRUCTURE:
            return tries < self.infrastructure_attempts
        if failure is MeasurementFailure.AMBIGUOUS:
            return tries < self.ambiguous_attempts
        return False


def terminal_failure(terminal: ExecutorObservation) -> MeasurementFailure | None:
    """Whose fault a terminal observation's failure is, or ``None`` when it did not fail."""
    if terminal.state is not EvaluationState.FAILED:
        return None
    return observed_failure(
        (
            evidence.failure_kind
            for step in terminal.stage_results
            if step.result is not None
            and (evidence := TrustedEvidence.model_validate(step.result)).outcome
            is EvidenceOutcome.FAILED
        ),
        run_environment_unavailable=terminal.run_environment_unavailable,
    )


def environment_refusal(terminal: ExecutorObservation) -> str | None:
    """Why the task's evaluator says its host refused the measurement, or ``None`` if it did not.

    The evaluator's declaration is typed (`EvidenceFailureKind.ENVIRONMENT_REFUSED`, read from
    its exit status); the words are only the reason an operator reads, the last line the
    refused stage printed, bounded.
    """
    if terminal.state is not EvaluationState.FAILED:
        return None
    for step in terminal.stage_results:
        if step.result is None:
            continue
        evidence = TrustedEvidence.model_validate(step.result)
        if (
            evidence.outcome is EvidenceOutcome.FAILED
            and evidence.failure_kind is EvidenceFailureKind.ENVIRONMENT_REFUSED
        ):
            return _reason_of(step.operator_failure or step.failure or "")
    return None


def _reason_of(text: str) -> str:
    """The refusal as one bounded line: the last non-empty line of what the stage printed."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    line = lines[-1] if lines else "the host refused the measurement"
    return ENVIRONMENT_WAIT_PREFIX + line[-_MAX_REASON_CHARS:]


def attempt_handle(handle_id: str, attempt: int) -> str:
    """The inner executor's handle for try number ``attempt`` (the first keeps the handle)."""
    return handle_id if attempt == 1 else f"{handle_id}-try-{attempt}"


class TryHandles:
    """Which try of which measurement each inner handle runs, as the wrapper submitted it.

    A ``RetryingEvaluationExecutor`` records every inner handle it submits here, so an
    inner executor's stage output can be named by the measurement's own handle (the one
    its progress reports name) and its try number. Share one with the wrapper and the
    inner executor's stage output opener; it is used on the executors' event loop only.
    """

    def __init__(self) -> None:
        """Know no tries yet."""
        self._tries: dict[str, tuple[str, int]] = {}

    def record(self, handle_id: str, attempt: int) -> None:
        """Remember that ``attempt_handle(handle_id, attempt)`` runs try ``attempt``."""
        self._tries[attempt_handle(handle_id, attempt)] = (handle_id, attempt)

    def stage_output(self, opener: StageOutputOpener) -> StageOutputOpener:
        """``opener``, called with the measurement's handle and try in place of the inner one.

        A stage of an inner handle this wrapper never submitted keeps its own handle and
        try 1: that executor runs it without retries.
        """

        def open_stage_output(run: StageRun) -> AbstractContextManager[OutputSink]:
            tried = self._tries.get(run.handle_id)
            if tried is None:
                return opener(run)
            handle_id, attempt = tried
            return opener(replace(run, handle_id=handle_id, attempt=attempt))

        return open_stage_output


class ClosableEvaluationExecutor(PollingEvaluationExecutor, Protocol):
    """A pollable executor the host releases at shutdown."""

    async def close(self) -> None:
        """Release the executor's workspaces and cancel its jobs."""
        ...


class _EnvironmentWait(BaseModel):
    """A measurement whose host refused it, held until the next run may start."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    until: float
    """The instant on the host's clock (seconds since the epoch) the next run may start."""
    reason: str = Field(min_length=1)
    """The pending reason reported while it waits."""


class _Tries(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    request: EvaluationRequest
    attempt: int = Field(default=1, ge=1)
    """The run the inner executor holds: its handle's suffix. Refused runs count here too."""
    cancelled: bool = Field(default=False, strict=True)
    refusals: int = Field(default=0, ge=0)
    """Runs the host refused. They are no try: the declared bounds count ``attempt - refusals``."""
    waiting: _EnvironmentWait | None = None
    """Set exactly while the host's last refusal holds the next run back."""


class RetryingEvaluationExecutor:
    """Measure again when a try fails for a reason that is not the candidate's."""

    def __init__(
        self,
        inner: ClosableEvaluationExecutor,
        retries: EvaluationRetries,
        namespace: EvaluationStateNamespace,
        *,
        clock: Clock,
        tries: TryHandles | None = None,
    ) -> None:
        """Retry ``inner``'s infrastructure failures within ``retries``, recorded in ``namespace``.

        ``clock`` times the wait after a host's refusal; it must count seconds since the epoch
        (`vs_sim.api.SystemClock` or a simulated one), because the wait is recorded in
        ``namespace`` and a restarted host reads it again. Each inner handle submitted is
        recorded in ``tries`` when one is given.
        """
        self._inner = inner
        self._tries = TryHandles() if tries is None else tries
        self._retries = retries
        self._namespace = namespace
        self._clock = clock

    async def availability(self, requirements: ResourceRequirements) -> AvailabilitySnapshot:
        """The inner executor's capacity."""
        return await self._inner.availability(requirements)

    async def submit(self, request: EvaluationRequest, *, handle_id: str) -> None:
        """Record the request, then start (or find) the current try."""
        tries = self._load(handle_id)
        if tries is None:
            tries = _Tries(request=request)
            self._namespace.save(self._path(handle_id), tries)
        elif not same_work(tries.request, request):
            message = f"retrying handle {handle_id!r} has different work"
            raise ExecutorRejectedError(message)
        elif tries.request != request:
            # The same work, recorded before its stages named their purpose: keep the
            # request as it is now asked for, so the tries to come carry the purpose.
            tries = tries.model_copy(update={"request": request})
            self._namespace.save(self._path(handle_id), tries)
        if not tries.cancelled:
            await self._submit_try(handle_id, tries)

    async def poll(self, handle_id: str) -> ExecutorPoll:
        """Inspect the current try; start the next one instead of reporting a retryable failure.

        A measurement its host refused reports as queued, with the refusal as its reason,
        until its backoff has passed; then the next run starts.
        """
        tries = self._load(handle_id)
        if tries is None:
            return await self._inner.poll(handle_id)
        if tries.waiting is None:
            return await self._poll_try(handle_id, tries)
        if tries.cancelled:
            # Nothing runs while the host's refusal holds the next run back, and the
            # measurement was cancelled: the refused run is no result of it.
            return self._cancelled()
        return await self._wait_out(handle_id, tries, tries.waiting)

    async def _poll_try(self, handle_id: str, tries: _Tries) -> ExecutorPoll:
        current = attempt_handle(handle_id, tries.attempt)
        polled = await self._inner.poll(current)
        if tries.cancelled:
            return await self._finish_cancel(current, polled)
        if polled.phase is PollPhase.UNSUBMITTED:
            # Every try is durable intent, including the first. A process-local
            # executor loses its tasks on restart; UNSUBMITTED proves it holds
            # none, so replay the same identity and plan without charging a retry.
            await self._submit_try(handle_id, tries)
            return ExecutorPoll(phase=PollPhase.QUEUED, attempt=polled.attempt)
        if polled.terminal is None:
            return polled
        if (reason := environment_refusal(polled.terminal)) is not None:
            return self._refused(handle_id, tries, reason)
        failure = terminal_failure(polled.terminal)
        if not self._retries.allows_another(failure, tries=tries.attempt - tries.refusals):
            return polled
        following = tries.model_copy(update={"attempt": tries.attempt + 1})
        self._namespace.save(self._path(handle_id), following)
        await self._submit_try(handle_id, following)
        return ExecutorPoll(phase=PollPhase.QUEUED, attempt=polled.attempt)

    def _refused(self, handle_id: str, tries: _Tries, reason: str) -> ExecutorPoll:
        """Hold the measurement after its host refused it: no try is spent, nothing is failed."""
        refusals = tries.refusals + 1
        waiting = _EnvironmentWait(
            until=self._clock.now() + self._retries.environment_delay(refusals), reason=reason
        )
        held = tries.model_copy(update={"refusals": refusals, "waiting": waiting})
        self._namespace.save(self._path(handle_id), held)
        return ExecutorPoll(phase=PollPhase.QUEUED, pending_reason=waiting.reason)

    async def _wait_out(
        self, handle_id: str, tries: _Tries, waiting: _EnvironmentWait
    ) -> ExecutorPoll:
        """Report the wait, or start the next run once the backoff has passed."""
        if self._clock.now() < waiting.until:
            return ExecutorPoll(phase=PollPhase.QUEUED, pending_reason=waiting.reason)
        following = tries.model_copy(update={"attempt": tries.attempt + 1, "waiting": None})
        self._namespace.save(self._path(handle_id), following)
        await self._submit_try(handle_id, following)
        return ExecutorPoll(phase=PollPhase.QUEUED, pending_reason=waiting.reason)

    @staticmethod
    def _cancelled() -> ExecutorPoll:
        return ExecutorPoll(
            phase=PollPhase.ENDED, terminal=ExecutorObservation(state=EvaluationState.CANCELED)
        )

    async def inspect_only(self, handle_id: str) -> ExecutorObservation | None:
        """The current try's observation, without dispatch."""
        if self._waiting(handle_id):
            return ExecutorObservation(state=EvaluationState.QUEUED)
        return await self._inner.inspect_only(self._current(handle_id))

    async def inspect(self, handle_id: str) -> ExecutorObservation | None:
        """The current try's observation; a measurement its host refused is queued, not failed."""
        if self._waiting(handle_id):
            return ExecutorObservation(state=EvaluationState.QUEUED)
        return await self._inner.inspect(self._current(handle_id))

    async def wait_for_change(self, handle_id: str, timeout_s: float) -> None:
        """Wait boundedly for the current try to change."""
        await self._inner.wait_for_change(self._current(handle_id), timeout_s)

    async def cancel(self, handle_id: str) -> None:
        """Cancel the current try; a cancelled try is never retried."""
        tries = self._load(handle_id)
        if tries is not None and not tries.cancelled:
            # Cancellation is durable intent too: a lost executor must not
            # resurrect work that the operator explicitly cancelled.
            self._namespace.save(
                self._path(handle_id), tries.model_copy(update={"cancelled": True})
            )
        await self._inner.cancel(self._current(handle_id))

    async def close(self) -> None:
        """Close the inner executor."""
        await self._inner.close()

    async def _finish_cancel(self, current: str, polled: ExecutorPoll) -> ExecutorPoll:
        """Recover cancellation committed before the previous host contacted its executor."""
        if polled.phase not in (PollPhase.ENDED, PollPhase.UNSUBMITTED):
            await self._inner.cancel(current)
            polled = await self._inner.poll(current)
        if polled.phase is PollPhase.UNSUBMITTED:
            return ExecutorPoll(
                phase=PollPhase.ENDED,
                terminal=ExecutorObservation(state=EvaluationState.CANCELED),
            )
        return polled

    async def _submit_try(self, handle_id: str, tries: _Tries) -> None:
        self._tries.record(handle_id, tries.attempt)
        await self._inner.submit(tries.request, handle_id=attempt_handle(handle_id, tries.attempt))

    def _waiting(self, handle_id: str) -> bool:
        tries = self._load(handle_id)
        return tries is not None and tries.waiting is not None and not tries.cancelled

    def _current(self, handle_id: str) -> str:
        tries = self._load(handle_id)
        return handle_id if tries is None else attempt_handle(handle_id, tries.attempt)

    def _load(self, handle_id: str) -> _Tries | None:
        return self._namespace.load_optional(self._path(handle_id), _Tries)

    @staticmethod
    def _path(handle_id: str) -> str:
        return f"evaluation-tries/{handle_id}.json"


__all__ = [
    "ENVIRONMENT_WAIT_PREFIX",
    "ClosableEvaluationExecutor",
    "EvaluationRetries",
    "RetryingEvaluationExecutor",
    "TryHandles",
    "attempt_handle",
    "environment_refusal",
    "terminal_failure",
]
