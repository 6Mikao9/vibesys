"""What workstreams tell the root while they run, and what the root answers each one.

A workstream reports on the `REPORTS` channel, in the order things happen to it: it submitted a
candidate for its trusted measurement (`Submission`), its measurement no longer holds a slot
(`Released`), it has waited for the input's measurement longer than the run allows
(`InputOverdue`). The root folds each report into its record and answers a submission on the
workstream's own `VERDICTS` latch: measure now, or never (`_queue.SlotVerdict`).
"""

from vibesys.orchestration.dynamic.search._queue import SlotVerdict, Submission
from vs_core.api.search import ChannelKey, LatchKey, Value


class Released(Value):
    """The trusted measurement of the workstream ``key`` is over: its slot is free."""

    key: str


class InputOverdue(Value):
    """The workstream ``key`` has waited ``limit`` seconds for the input's measurement."""

    key: str
    since: float
    """When it began to wait, on the run clock."""
    limit: float


type Report = Submission | Released | InputOverdue

REPORTS = ChannelKey[Report]("workstream-reports")
"""Every workstream reports here; the root reads it."""

VERDICTS = LatchKey[SlotVerdict]("evaluation-verdict")
"""The root's answer to a submission, one latch per workstream key (``VERDICTS.of(key)``)."""
