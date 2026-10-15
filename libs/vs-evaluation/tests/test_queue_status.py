"""The queue status reader's Fake passes the contract, and the agents' tool only reads.

The tool server is exercised as an agent would call it, through ``FastMCP.list_tools`` and
``call_tool`` on synthesized calls: it offers exactly one tool, which takes no arguments,
refuses any it is given, and answers the reader's status as JSON on every call.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st
from mcp.server.fastmcp.exceptions import ToolError

from vs_evaluation.api import QueueItem, QueueItemKind, QueueItemState, QueueLoad, QueueStatus
from vs_evaluation.api.testing import (
    FakeQueueStatus,
    QueueStatusHarness,
    QueueStatusReaderContract,
    QueueWorld,
)
from vs_evaluation.api.tools import (
    QUEUE_STATUS_SERVER,
    QUEUE_STATUS_TOOL,
    QUEUE_STATUS_TOOLS,
    build_queue_status_tools,
)
from vs_mcp.api import tool_server

if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP


def _status_of(world: QueueWorld) -> QueueStatus:
    queued = tuple(
        QueueItem(
            job=f"q{index}",
            kind=QueueItemKind.EVALUATION,
            state=QueueItemState.QUEUED,
            position=index + 1,
        )
        for index in range(world.queued)
    )
    running = tuple(
        QueueItem(job=f"r{index}", kind=QueueItemKind.AGENT_GPU_JOB, state=QueueItemState.RUNNING)
        for index in range(world.running)
    )
    load = QueueLoad(pool="gpu", pending=world.queued, running=world.running + world.others)
    return QueueStatus(readable=True, items=queued + running, loads=(load,))


class TestFakeQueueStatus(QueueStatusReaderContract):
    def harness(self) -> QueueStatusHarness:
        return QueueStatusHarness(
            reporting=lambda world: FakeQueueStatus(_status_of(world)),
            unreachable=FakeQueueStatus,
            queues=True,
        )


async def _call(server: FastMCP, arguments: dict[str, object]) -> str:
    """The tool's text answer; ``call_tool`` returns (content, structured) for a str tool."""
    _, structured = cast(
        "tuple[object, dict[str, Any]]", await server.call_tool(QUEUE_STATUS_TOOL, arguments)
    )
    return structured["result"]


def _server(reader: FakeQueueStatus) -> FastMCP:
    return tool_server(build_queue_status_tools(reader), server_name=QUEUE_STATUS_SERVER)


def test_the_server_offers_exactly_the_one_read_only_tool_and_it_takes_no_arguments() -> None:
    tools = asyncio.run(_server(FakeQueueStatus()).list_tools())

    assert tuple(tool.name for tool in tools) == QUEUE_STATUS_TOOLS == (QUEUE_STATUS_TOOL,)
    (tool,) = tools
    assert not tool.inputSchema.get("properties")
    assert not tool.inputSchema.get("required")


worlds = st.builds(
    QueueWorld,
    queued=st.integers(0, 4),
    running=st.integers(0, 4),
    others=st.integers(0, 20),
)


@given(world=worlds, calls=st.integers(1, 3))
def test_each_call_answers_what_the_reader_reads_now(world: QueueWorld, calls: int) -> None:
    reader = FakeQueueStatus(_status_of(world))
    server = _server(reader)

    answers = [asyncio.run(_call(server, {})) for _ in range(calls)]

    assert [QueueStatus.model_validate_json(answer) for answer in answers] == [
        reader.status
    ] * calls
    assert reader.reads == calls


def test_an_unreadable_backend_answers_unreadable_rather_than_an_error() -> None:
    answer = asyncio.run(_call(_server(FakeQueueStatus()), {}))

    assert QueueStatus.model_validate_json(answer) == QueueStatus.unreadable()


@pytest.mark.parametrize("arguments", [{"job": "41"}, {"cancel": True}, {"partition": "gpu"}])
def test_an_argument_is_refused_and_nothing_is_read(arguments: dict[str, object]) -> None:
    reader = FakeQueueStatus()

    with pytest.raises(ToolError):
        asyncio.run(_call(_server(reader), arguments))

    assert reader.reads == 0
