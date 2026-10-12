"""Constructed `RunView`s for strategy tests (public core API only)."""

from __future__ import annotations

from vs_core.api import RunFacts, RunView, run_view
from vs_core.testing.builders import initial_state


def empty_view(facts: RunFacts | None = None) -> RunView:
    """A run with no attempts or evidence, carrying ``facts`` when given."""
    view = run_view(initial_state())
    return view if facts is None else view.model_copy(update={"facts": facts})
