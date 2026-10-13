"""The designer both serial searches ask, declared once.

Single and multi design the next hypothesis with the same agent: it reads the search so far,
writes the roadmap artifacts of its run and answers with an `OrchestratorPlan`. The searches
declare their other agents beside themselves.
"""

from vibesys.hypothesis import OrchestratorPlan
from vs_core.api.search import Access, Backoff, Charge, Exchanges, Role, minutes

LOST_TURN_BACKOFF = Backoff(first=5.0, cap=120.0)
"""A lost turn is asked again after this wait on the run clock, doubling per loss."""

ORCHESTRATOR = Role(
    name="orchestrator",
    prompt="plan.j2",
    reply=OrchestratorPlan,
    workspace=None,
    access=Access.WRITE_ARTIFACTS,
    turn_time=minutes(30),
    corrections=1,
    lost_turns=2,
    lost_turn_backoff=LOST_TURN_BACKOFF,
    charge=Charge.TURN,
    exchanges=Exchanges(correction="reply_correction.j2"),
)
"""Designs one hypothesis of the next round; works in the run, not in a candidate."""
