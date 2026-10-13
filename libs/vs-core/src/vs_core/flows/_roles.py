"""The minimal role value ``ctx.agents`` needs, and the prompts of core's own exchanges.

A role says who an agent is, what it answers with and how core retries it. This is the seam
the role declaration of the next change will grow into; until then it holds exactly what the
turn mechanics of ``ctx.agents`` read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from vs_core.types.agents import ResumeFacts, Retry
from vs_core.types.until import ReplyError, ReplyErrorCode

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel, ValidationError

    from vs_core.types.common import SchemaRef, Value, WorkspaceMode
    from vs_core.types.sessions import Access


@dataclass(frozen=True)
class Role:
    """One agent a search may ask: who it is, what it answers with and how core retries it."""

    name: str
    reply: type[BaseModel]
    """The wire model of the reply (a ``WaitableReply[R]`` for a role that may wait)."""
    schema: SchemaRef
    access: Access
    workspace: WorkspaceMode | None
    """The mode of the role's workspace revision, or None for a role that works in the run."""
    charge: Literal["free", "paid"]
    retry: Retry = field(default_factory=Retry)
    waits: bool = False
    """Whether the role may end a turn waiting for its evaluations (its reply is waitable)."""
    checkpoint: Callable[[BaseModel], bool] | None = None
    """Whether a reply must wait for core's checkpoint of the turn before it is delivered."""


@dataclass(frozen=True)
class AgentProtocol:
    """The prompt contexts of the exchanges core itself starts: corrections and resumes."""

    correction: Callable[[str, tuple[ReplyError, ...]], Value]
    resume: Callable[[ResumeFacts], Value]
    wait_unrecorded: Callable[[str], Value]


def reply_errors(error: ValidationError) -> tuple[ReplyError, ...]:
    """The typed problems of a reply that broke its model."""
    found: list[ReplyError] = []
    for item in error.errors():
        kind = item["type"]
        if kind == "missing":
            code = ReplyErrorCode.MISSING
        elif kind == "extra_forbidden":
            code = ReplyErrorCode.UNEXPECTED
        elif kind == "json_invalid":
            code = ReplyErrorCode.NOT_JSON
        elif kind.endswith(("_type", "_parsing")):
            code = ReplyErrorCode.WRONG_TYPE
        else:
            code = ReplyErrorCode.INVALID_VALUE
        path = tuple(part for part in item["loc"] if isinstance(part, str | int))
        found.append(ReplyError(path=path, code=code))
    return tuple(found) or (ReplyError(path=(), code=ReplyErrorCode.INVALID_VALUE),)
