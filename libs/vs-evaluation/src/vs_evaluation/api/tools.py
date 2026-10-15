"""Public MCP tool adapter for the evaluation agent service."""

from vs_evaluation.agent_core_mcp import (
    CORE_EVALUATION_TOOLS,
    SUBMIT_TOOL,
    VALIDATE_WAIT_TOOL,
    build_core_evaluation_tools,
    core_evaluation_mcp_descriptor,
)
from vs_evaluation.agent_wire import EvaluationServiceClientError, SocketClient
from vs_evaluation.queue_status_mcp import (
    QUEUE_STATUS_SERVER,
    QUEUE_STATUS_TOOL,
    QUEUE_STATUS_TOOLS,
    QueueStatusArgs,
    build_queue_status_tools,
)

__all__ = [
    "CORE_EVALUATION_TOOLS",
    "QUEUE_STATUS_SERVER",
    "QUEUE_STATUS_TOOL",
    "QUEUE_STATUS_TOOLS",
    "SUBMIT_TOOL",
    "VALIDATE_WAIT_TOOL",
    "EvaluationServiceClientError",
    "QueueStatusArgs",
    "SocketClient",
    "build_core_evaluation_tools",
    "build_queue_status_tools",
    "core_evaluation_mcp_descriptor",
]
