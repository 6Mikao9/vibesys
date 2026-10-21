"""How long a request may take, what else ends it, and the typed failure that reports it.

A request carries no deadline. The one who asks for work states, next to the request,
``Within(limit)`` (a duration on the run clock) extended with the conditions that make the
work pointless before the limit: ``Within(60) | WhileOpen(workspace) | WhileAlive(job)``.
Core stores the accepted form (``Until``, an absolute time) on the request record. When the
limit passes or a watch fires, the record ends and its owner receives ``Failed(reason)``
with ``TIMED_OUT``, ``WORKSPACE_CLOSED`` or ``OWNER_GONE``.

A watch is not a bound: ``WhileOpen(ws)`` alone is not a valid ``until``, only ``Within``
is, so every wait has a limit.
"""

from __future__ import annotations

from vs_core._generated.vibesys.core.v1.failures_cv import (
    REPLY_ERROR_MESSAGE_LIMIT,
    Failed,
    ReplyError,
)
from vs_core._generated.vibesys.core.v1.leaves_cv import (
    FromDispatch,
    Inherited,
    Limit,
    Until,
    Watch,
    WhileAlive,
    WhileOpen,
    Within,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    FailureReason,
    ReplyErrorCode,
    WaitRefusal,
)

__all__ = [
    "REPLY_ERROR_MESSAGE_LIMIT",
    "Failed",
    "FailureReason",
    "FromDispatch",
    "Inherited",
    "Limit",
    "ReplyError",
    "ReplyErrorCode",
    "Until",
    "WaitRefusal",
    "Watch",
    "WhileAlive",
    "WhileOpen",
    "Within",
]
