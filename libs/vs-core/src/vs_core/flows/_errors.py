"""The two exceptions of the flows runtime."""


class Stopped(Exception):  # noqa: N818  # LW-994801 [N818]; read as an event ("the child was Stopped"), like asyncio's CancelledError, not as an error condition.
    """Thrown at the await a ``stoppable`` child is suspended on when the operator stops the run.

    The child may catch it and await more (to wind down). Letting it escape is a bug.
    """


class FlowCodeChanged(Exception):  # noqa: N818  # LW-994802 [N818]; the name states what happened, as the replay check reports it.
    """The flow code no longer reproduces the journal: replay diverged at ``step``."""

    def __init__(self, step: int) -> None:
        """Name the journal step at which the code and the journal disagree."""
        super().__init__(f"the flow code diverged from its journal at step {step}")
        self.step = step
