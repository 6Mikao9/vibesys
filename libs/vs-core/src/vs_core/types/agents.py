"""What a search says about the agents it asks, and what the agent pipeline hands back.

A search names an agent in ``AskAgent`` and gets one ``TurnFinished`` or one ``Failed``. How
the reply is rendered, validated, corrected, asked again and resumed is core's, set per
agent by ``Retry``. These are the values both sides share.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field

from .common import CheckpointDecline, InvocationRef, RevisionRef, Seconds, Value
from .evaluation import EvidenceRef
from .evaluation_history import RepeatedFailureGuidance


class Backoff(Value):
    """Run-clock wait before a lost turn is asked again; each further ask doubles it."""

    first: Seconds = 5.0
    cap: Seconds = 120.0

    def delay(self, drops: int) -> float:
        """Seconds to wait before re-asking a turn already lost ``drops`` times."""
        return min(self.cap, self.first * 2**drops)


class Retry(Value):
    """How many times core asks again on its own before it reports the failure.

    ``corrections`` bounds the corrections of an invalid reply; ``lost_turns`` bounds the
    asks after a turn the transport lost, each after ``backoff``. ``Retry.none()`` reports
    ``Failed(INVALID_REPLY)`` or ``Failed(LOST)`` at once, for an agent whose search asks
    again itself (with a fresh prompt, say).
    """

    corrections: Annotated[int, Field(ge=0)] = 1
    lost_turns: Annotated[int, Field(ge=0)] = 2
    backoff: Backoff = Backoff()

    @classmethod
    def none(cls) -> Retry:
        """No automatic corrections and no automatic asks after a lost turn."""
        return cls(corrections=0, lost_turns=0)


class Correction(Value):
    """Base of an input that corrects an earlier turn of the same agent.

    A search that handles invalid replies itself asks again with an input of a subclass
    carrying why the earlier reply failed. The pipeline sends the new turn as a correction
    of ``of``, as core requires.
    """

    of: InvocationRef


class ResumeFacts(Value):
    """What the resume prompt of a suspended turn is built from."""

    agent: str
    retained: RevisionRef | None
    timed_out: bool
    evidence: tuple[EvidenceRef, ...] = ()
    repeated_failure: RepeatedFailureGuidance | None = None


class Retained(Value):
    """Core retained the exact revision this turn produced."""

    kind: Literal["retained"] = "retained"
    revision: RevisionRef


class Declined(Value):
    """Core declined or failed this turn's checkpoint."""

    kind: Literal["declined"] = "declined"
    reason: CheckpointDecline


type Checkpoint = Annotated[Retained | Declined, Field(discriminator="kind")]
