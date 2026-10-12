"""Reading a run's decisions as the work they ask for."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_core.api import Wait

if TYPE_CHECKING:
    from collections.abc import Iterable

    from vs_core.api import Decision, Finish, WakeAt, Work


def works(decisions: Iterable[Decision]) -> list[Work | Finish | WakeAt]:
    """Each decision as its work: a wait is the request it waits for."""
    return [item.request if isinstance(item, Wait) else item for item in decisions]
