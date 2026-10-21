"""What a search says about the agents it asks, and what the agent pipeline hands back.

A search asks an agent through ``ctx.agents`` and gets one ``TurnFinished`` or one ``Failed``.
How the reply is rendered, validated, corrected, asked again and resumed is core's mechanism,
set per agent by its ``Role``. These are the values both sides share.
"""

from __future__ import annotations

from vs_core._generated.vibesys.core.v1.agent_facts_cv import (
    ProtocolContext,
    ReplyCorrection,
    ResumeFacts,
)
from vs_core._generated.vibesys.core.v1.leaves_cv import (
    Backoff,
    ChargeClass,
    Checkpoint,
    Declined,
    Exchanges,
    Retained,
    WaitUnrecorded,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    Charge,
    Correctable,
)

from .common import (
    InvocationRef,
    Value,
)

"""The charge class of a turn request: a role's own (free or paid), a correction or a resume."""


class Correction(Value):
    """Base of an input that corrects an earlier turn of the same agent.

    A search that handles invalid replies itself asks again with an input of a subclass
    carrying why the earlier reply failed. The pipeline sends the new turn as a correction
    of ``of``, as core requires.
    """

    of: InvocationRef


"""The contexts of the exchanges core starts: what their templates render."""

__all__ = [
    "Backoff",
    "Charge",
    "ChargeClass",
    "Checkpoint",
    "Correctable",
    "Correction",
    "Declined",
    "Exchanges",
    "ProtocolContext",
    "ReplyCorrection",
    "ResumeFacts",
    "Retained",
    "WaitUnrecorded",
]
