"""The agents' read-only queue status tool: one MCP tool that reports the run's queue.

``build_queue_status_tools`` wraps a ``QueueStatusReader`` in the one tool, ``queue_status``,
which takes no arguments and answers the reader's ``QueueStatus`` as JSON. The server offers
no other tool: nothing here can submit, cancel or change any job, the run's or anyone
else's. Which reader backs it, and in which process it is served, is the host's to choose.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from vs_mcp.api import ToolSpec

if TYPE_CHECKING:
    from vs_evaluation.queue_status import QueueStatusReader

QUEUE_STATUS_SERVER = "vs-queue-status"
QUEUE_STATUS_TOOL = "queue_status"
QUEUE_STATUS_TOOLS = (QUEUE_STATUS_TOOL,)


class QueueStatusArgs(BaseModel):
    """The queue status tool takes no arguments."""

    model_config = ConfigDict(extra="forbid")


def build_queue_status_tools(reader: QueueStatusReader) -> tuple[ToolSpec[QueueStatusArgs], ...]:
    """The one read-only tool, answering what ``reader`` reads at each call."""

    def answer(_: QueueStatusArgs) -> str:
        return reader.read().model_dump_json()

    return (
        ToolSpec(
            name=QUEUE_STATUS_TOOL,
            description=(
                "Read the evaluation queue this run's jobs wait in. Returns the run's own "
                "evaluations and GPU jobs (queued or running, seconds waited, place in line "
                "and expected start when known) and how busy each pool is (jobs pending and "
                "running, GPUs total and in use). A null field is one the cluster does not "
                "report; readable=false means the cluster could not be asked. Read-only: it "
                "changes no job."
            ),
            input_schema=QueueStatusArgs,
            handler=answer,
        ),
    )
