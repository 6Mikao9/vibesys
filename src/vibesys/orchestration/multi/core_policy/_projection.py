"""Read model of a multi run: the hypothesis ledger in the shape frontends already consume."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.hypothesis.readmodel import project_hypothesis_state

if TYPE_CHECKING:
    from vibesys.hypothesis import HypothesisState
    from vibesys.run.contracts import PluginProjection


def project_run_snapshot(
    snapshot: HypothesisState, *, experiment_revision: int
) -> PluginProjection:
    """Project the published ledger; `experiment_revision` is the core revision it was read at."""
    del experiment_revision
    return project_hypothesis_state(snapshot)
