"""Public test doubles for the evaluation lifecycle API."""

from vs_evaluation.queue_status_contracts import (
    QueueStatusHarness,
    QueueStatusReaderContract,
    QueueWorld,
)
from vs_evaluation.started_operation import wait_until_executor_started
from vs_evaluation.testing import (
    FakeDeadlineFactory,
    FakeDeadlineScope,
    FakeEvaluationBackend,
    FakeEvaluationExecutor,
    FakeQueueStatus,
    FakeSubmission,
    InMemoryEvaluationNamespace,
    InMemoryEvaluationStore,
)

__all__ = [
    "FakeDeadlineFactory",
    "FakeDeadlineScope",
    "FakeEvaluationBackend",
    "FakeEvaluationExecutor",
    "FakeQueueStatus",
    "FakeSubmission",
    "InMemoryEvaluationNamespace",
    "InMemoryEvaluationStore",
    "QueueStatusHarness",
    "QueueStatusReaderContract",
    "QueueWorld",
    "wait_until_executor_started",
]
