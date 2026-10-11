"""The lifecycle bookkeeping a strategy keeps for one attempt it started.

Core owns the attempt lifecycle (slots, generations, settlement). A strategy still has to
remember, per attempt, where its own conversation with core stands: which step of a phase it
is in, which decision it awaits, whether core admitted the attempt, whether it already asked
to settle, and whether a write turn's reply is held until core reads the workspace
checkpoint. Those facts and the protocol rules over them live here once:

- An attempt that was never admitted and fails *refunds* its slot (nothing to settle); an
  admitted one must still settle, so a failure moves it to its settle step.
- A write turn's reply reaches the strategy before the executor snapshots the workspace, so
  the reply is *held* (`Step.RETAINING`) until the candidate is readable from the view.

A strategy subclasses `AttemptTrack` to add its scientific fields, so its persisted state keeps
one flat shape. Every transition returns a new track and never proposes a decision.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import Field

from vs_core._turn_chain import TurnChain
from vs_core.types.common import AttemptId, DecisionId, InvocationRef, Value


class Step(StrEnum):
    """Progress of the current phase of one subject through propose, wait, settle.

    NEEDED: decide must propose the phase's next decision. RENDERING / RENDERED:
    a prompt render is in flight or done. AWAITING: a decision or turn is in
    flight and an event moves the subject on. SUSPENDED: the turn yielded to wait
    for its evaluations and only `ResumeAuthorized` moves it on. RETAINING: a write
    turn answered, but core has not yet retained (or declined) its checkpoint, so
    the reply is held until `decide` can read the candidate from the view.
    """

    NEEDED = "needed"
    RENDERING = "rendering"
    RENDERED = "rendered"
    AWAITING = "awaiting"
    SUSPENDED = "suspended"
    RETAINING = "retaining"


class AttemptTrack[T: TurnChain](Value):
    """Where one attempt stands in the strategy's conversation with core."""

    attempt: AttemptId
    generation: int = Field(default=0, ge=0)
    step: Step = Step.NEEDED
    awaiting: DecisionId | None = None
    turn: T | None = None
    failure: str | None = None
    # Core has admitted the attempt, so leaving it requires a settlement.
    ready: bool = False
    settle_sent: bool = False
    # The write-turn reply (JSON) and its turn waiting for the checkpoint, while RETAINING.
    held_reply: str | None = None
    held_invocation: InvocationRef | None = None

    @property
    def owes_settlement(self) -> bool:
        """Whether leaving the attempt requires a settlement (core admitted it)."""
        return self.ready

    def awaits(self, decision: str | None) -> bool:
        """Whether the track waits on the decision with this identity."""
        return decision is not None and self.awaiting is not None and self.awaiting.root == decision

    def admitted(self, generation: int) -> Self:
        """Core admitted the attempt: it holds a slot at ``generation``."""
        return self.model_copy(update={"ready": True, "generation": generation, "awaiting": None})

    def failed(self, reason: str) -> Self:
        """Record the first failure; an admitted attempt goes back to propose its settlement.

        The caller moves its own phase: to its settle phase when `owes_settlement`, else to
        done, refunding the slot the unadmitted attempt never used.
        """
        update: dict[str, object] = {"failure": self.failure or reason, "awaiting": None}
        if self.ready:
            update["step"] = Step.NEEDED
        return self.model_copy(update=update)

    def holding(self, reply: str, invocation: InvocationRef) -> Self:
        """Hold a write turn's reply until core retains or declines its checkpoint."""
        return self.model_copy(
            update={
                "step": Step.RETAINING,
                "awaiting": None,
                "held_reply": reply,
                "held_invocation": invocation,
            }
        )

    @property
    def holds_reply(self) -> bool:
        """Whether a reply is held and its checkpoint is still to be read."""
        return (
            self.step is Step.RETAINING
            and self.held_invocation is not None
            and self.held_reply is not None
        )

    def released(self) -> Self:
        """The held reply was taken up: propose the phase's next decision."""
        return self.model_copy(
            update={"step": Step.NEEDED, "held_reply": None, "held_invocation": None}
        )
