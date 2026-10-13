"""The exceptions and plain values of the flows runtime."""

from vs_core.types.common import Seconds, Value


class Stopped(Exception):  # noqa: N818  # LW-994801 [N818]; read as an event ("the task was Stopped"), like asyncio's CancelledError, not as an error condition.
    """Thrown at an unshielded ``await`` of a task in a stopping group when the operator stops.

    Cleanup belongs in a ``@resource``; a task that lets it escape ends as ``TaskStopped``.
    """


class FlowCodeChanged(Exception):  # noqa: N818  # LW-994802 [N818]; the name states what happened, as the replay check reports it.
    """The flow code no longer reproduces the journal: replay diverged at ``step``."""

    def __init__(self, step: int) -> None:
        """Name the journal step at which the code and the journal disagree."""
        super().__init__(f"the flow code diverged from its journal at step {step}")
        self.step = step


class Deadlock(Exception):  # noqa: N818  # LW-994804 [N818]; the name is the situation the flow is in, as the contract document calls it.
    """A flow waits only for sources that can never yield again: a bug in the flow."""

    def __init__(self, flow: str, sources: tuple[str, ...]) -> None:
        """Name the flow and what it was waiting for."""
        super().__init__(f"flow {flow or 'root'} waits for {', '.join(sources) or 'nothing'}")
        self.flow = flow
        self.sources = sources


class TimeUp(Value):
    """The value of ``ctx.timer(at)``: the run clock reached ``at``."""

    at: Seconds


class TaskStopped(Value):
    """A task ended because a stop reached it and it let ``Stopped`` escape."""

    key: str
