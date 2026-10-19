"""The host helper's ``usage.window``: what the runs of a project spent in a window of time.

The desktop's Usage page shows, across a project's runs, what was spent and by whom in a window
(the last day, the last two weeks): the total, the rate now, the tokens, the cost of each new best
result, and the cost by run and by agent. ``usage.window`` answers it from the runs' usage records
(``server.run_usage``, the same records ``runs.summary`` serves) and the landing times of the ideas
each run kept (``IdeaPoint.at``), so a client neither pages every run's records nor folds them.
``server.usage_window`` is the pure aggregation; this module holds the messages.

Every limit below bounds the work and the reply, so the largest answer stays far under
``HELPER_LINE_MAX_BYTES`` (a test builds it): a request names at most ``USAGE_WINDOW_MAX_RUNS``
runs and a window of at most ``USAGE_WINDOW_MAX_HOURS`` hours, and a reply names at most one entry per
requested run and ``USAGE_WINDOW_MAX_AGENTS`` agents.

Light: Pydantic, the standard library, ``server.host_envelope`` and ``server.host_run_summary``.
``server.host_api`` imports these messages into the helper's request and reply unions and gates them
behind ``HelperFeature.USAGE_WINDOW``.

Public surface: ``UsageWindowRequest``, ``UsageWindowReply``, ``RunSpend``, ``AgentSpend`` and the
limits ``USAGE_WINDOW_MAX_RUNS``, ``USAGE_WINDOW_MAX_AGENTS``, ``USAGE_WINDOW_MAX_HOURS`` and
``RATE_WINDOW_S``.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Annotated, Final, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    model_validator,
)

from server.host_envelope import HelperReplyBase, HelperRequestBase, RunId
from server.host_run_summary import TokenTally

USAGE_WINDOW_MAX_RUNS: Final = 100
"""The most runs one request names (the size of a ``runs.list`` digest page), so the work is bounded."""
USAGE_WINDOW_MAX_AGENTS: Final = 64
"""The most agents (a role, harness and model) one reply lists; the costliest come first."""
USAGE_WINDOW_MAX_HOURS: Final = 336
"""The longest window, in hours: two weeks."""
RATE_WINDOW_S: Final = 3600
"""The trailing seconds, ending at the window's end, that ``spend_per_hour_usd`` is read over."""


class UsageWindowRequest(HelperRequestBase):
    """What the named runs of a project spent in ``[since, until)``.

    ``until`` is "now" for the reply: the helper reads no clock for it, so a client names the time it
    is asking at (or the end of a past window) and the answer is a function of the journals and
    these two instants. A turn is placed at the time it ended (``UsageRecord.ended_at``).
    """

    type: Literal["usage.window"] = "usage.window"
    project: str
    """The project directory on the host; ``~`` expands to the helper's home."""
    run_ids: Annotated[tuple[RunId, ...], Field(max_length=USAGE_WINDOW_MAX_RUNS)]
    """The runs to count, as ``runs.list`` names them; a run the project does not have is reported
    in the reply's ``missing_runs``. A repeated id counts once."""
    since: AwareDatetime
    until: AwareDatetime

    @model_validator(mode="after")
    def _window_is_bounded(self) -> Self:
        if not self.since < self.until:
            message = "since must be before until"
            raise ValueError(message)
        if self.until - self.since > timedelta(hours=USAGE_WINDOW_MAX_HOURS):
            message = f"the window may span at most {USAGE_WINDOW_MAX_HOURS} hours"
            raise ValueError(message)
        return self


class RunSpend(BaseModel):
    """What one run spent in the window; only runs that had a turn end in it are listed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    run_id: RunId
    task: str | None
    """The task the run was started for, as ``runs.list`` names it; ``None`` when the run records none."""
    cost_usd: NonNegativeFloat
    """The reported cost of the run's turns that ended in the window."""


class AgentSpend(BaseModel):
    """What one kind of agent spent in the window: a role on a harness and model.

    A part the journal did not record for a turn is ``None``, so the turns of an execution whose
    start is missing form their own entry.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    role: str | None
    harness: str | None
    model: str | None
    cost_usd: NonNegativeFloat
    turns: NonNegativeInt
    """The turns, priced or not, that ended in the window."""


class UsageWindowReply(HelperReplyBase):
    """What the Usage page shows for one window.

    Costs are the sums of the turns' reported ``cost_usd`` (a turn that reported none counts in
    ``unpriced_turns`` and adds nothing, so a total is a lower bound while it is not zero). A turn
    with no recorded end time cannot be placed in a window: it counts in ``untimed_turns`` and
    nowhere else. ``new_bests`` counts the ideas kept (``IdeaVerdict.KEPT``) whose result landed in
    the window, and ``cost_per_new_best_usd`` is ``spent_usd`` over it, ``None`` when there is none.
    A run's kept ideas beyond ``DASHBOARD_MAX_POINTS`` are not counted.
    """

    type: Literal["usage.window"] = "usage.window"
    since: AwareDatetime
    until: AwareDatetime
    spent_usd: NonNegativeFloat
    spend_per_hour_usd: NonNegativeFloat
    """The cost of the turns that ended in the last ``min(RATE_WINDOW_S, window)`` seconds before
    ``until``, per hour of that span."""
    tokens: TokenTally
    """The tokens of the turns in the window, summed as a run's are."""
    turns: NonNegativeInt
    unpriced_turns: NonNegativeInt
    untimed_turns: NonNegativeInt
    new_bests: NonNegativeInt
    cost_per_new_best_usd: NonNegativeFloat | None
    by_run: Annotated[tuple[RunSpend, ...], Field(max_length=USAGE_WINDOW_MAX_RUNS)]
    """The costliest run first (ties by run id)."""
    by_agent: Annotated[tuple[AgentSpend, ...], Field(max_length=USAGE_WINDOW_MAX_AGENTS)]
    """The costliest agent first (ties by role, harness, model)."""
    agents_omitted: NonNegativeInt
    """The agents that spent in the window beyond ``USAGE_WINDOW_MAX_AGENTS``."""
    missing_runs: Annotated[tuple[RunId, ...], Field(max_length=USAGE_WINDOW_MAX_RUNS)]
    """The requested runs the project does not have, in the order asked."""
