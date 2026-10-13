"""The event an attempt area publishes when an invocation's checkpoint is settled."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .types.agents import Declined, Retained
from .types.attempts import CheckpointResolved

if TYPE_CHECKING:
    from .types.attempts import CheckpointDecline
    from .types.common import InvocationRef, RevisionRef


def declined(
    invocation: InvocationRef, reason: CheckpointDecline
) -> tuple[CheckpointResolved, ...]:
    """The published fact that ``invocation``'s checkpoint was declined for ``reason``."""
    return (CheckpointResolved(invocation=invocation, checkpoint=Declined(reason=reason)),)


def retained(invocation: InvocationRef, revision: RevisionRef) -> tuple[CheckpointResolved, ...]:
    """The published fact that ``invocation``'s checkpoint was recorded at ``revision``."""
    return (CheckpointResolved(invocation=invocation, checkpoint=Retained(revision=revision)),)
