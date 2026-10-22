"""The run's read-only queue status: which reader its environment gets, and where it is served.

The host owns the backend reader, because only the host can reach the scheduler; agents
reach it through the queue status tool server, which reads this service over a unix
socket beside the evaluation socket (one mounted directory serves both). One cached
reading serves every agent for ``FRESH_FOR_S`` seconds.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from vs_core.api import FlowState
from vs_evaluation.api.queue_status import (
    FRESH_FOR_S,
    CachedQueueStatusReader,
    EvaluationOccupancy,
    LocalQueueStatusReader,
    OpaqueQueueStatusReader,
    QueueStatusService,
)
from vs_runtime.api.core import RuntimeRecord
from vs_sandbox.api.slurm import slurm_queue_status_reader

if TYPE_CHECKING:
    from pathlib import Path

    from vs_evaluation.api.queue_status import QueueStatusReader
    from vs_runtime.api.infrastructure import RunEnvironmentView
    from vs_sim.api import Clock, Network, Processes, Threads

#: Run environments whose evaluations wait in a service that reports no queue.
_OPAQUE_ENVIRONMENTS = frozenset({"modal", "skypilot"})

# The dynamic search publishes this kind on its run record (`RunSnapshot.kind`).
_DYNAMIC_SNAPSHOT = "dynamic-run-snapshot"


def published_search_waiting(published: str | None) -> int:
    """How many candidates a published dynamic snapshot has waiting for a slot.

    The count is the search queue's ``pending`` list, in pick-policy order. Any
    other published record has none waiting.
    """
    if not published:
        return 0
    try:
        data = json.loads(published)
    except json.JSONDecodeError:
        return 0
    if not isinstance(data, dict) or data.get("kind") != _DYNAMIC_SNAPSHOT:
        return 0
    evaluations = data.get("evaluations")
    if not isinstance(evaluations, dict):
        return 0
    pending = evaluations.get("pending")
    return len(pending) if isinstance(pending, list) else 0


def search_waiting_of(state: object) -> int:
    """Candidates waiting for a slot in a committed dynamic run; 0 otherwise."""
    if not isinstance(state, RuntimeRecord):
        return 0
    strategy = state.envelope.strategy
    if not isinstance(strategy, FlowState):
        return 0
    return published_search_waiting(strategy.live.published)


def queue_status_reader(  # noqa: PLR0913  # lint-waiver: LW-221004 [PLR0913]; occupancy is the local queue's count source, beside the environment the reader already selects. Building the local reader in the host would duplicate that selection, and a wrapper would only store the same object.
    view: RunEnvironmentView,
    *,
    run_id: str,
    handle_root: Path,
    processes: Processes,
    clock: Clock,
    occupancy: EvaluationOccupancy | None = None,
) -> QueueStatusReader:
    """The reader for the run's environment: its Slurm cluster, an opaque service, or this host.

    ``handle_root`` is the cluster evaluation executor's, whose records name the run's
    evaluation jobs; ``processes`` reach the cluster as the executor's do.
    """
    cluster = view.cluster_evaluation
    if cluster is not None:
        return slurm_queue_status_reader(
            cluster.settings,
            run_id=run_id,
            handle_root=handle_root,
            processes=processes,
            clock=clock,
        )
    if view.env_kind in _OPAQUE_ENVIRONMENTS:
        return OpaqueQueueStatusReader()
    return LocalQueueStatusReader(occupancy)


def queue_status_service(
    reader: QueueStatusReader,
    socket_path: Path,
    *,
    network: Network,
    threads: Threads,
    clock: Clock,
) -> QueueStatusService:
    """The service of ``reader``, cached for ``FRESH_FOR_S``, at ``socket_path``; not yet started.

    The caller prepares the socket's private directory (``prepare_queue_status_socket``),
    registers ``close`` with the run's release stack, then starts it.
    """
    return QueueStatusService(
        CachedQueueStatusReader(reader, clock=clock, threads=threads, fresh_for_s=FRESH_FOR_S),
        socket_path,
        network=network,
        threads=threads,
        clock=clock,
    )


__all__ = [
    "EvaluationOccupancy",
    "published_search_waiting",
    "queue_status_reader",
    "queue_status_service",
    "search_waiting_of",
]
