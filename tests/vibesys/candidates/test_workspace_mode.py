"""The one rule from a host's capability to the mode candidates work in."""

from __future__ import annotations

from vibesys.candidates.api import workspace_mode_for
from vs_core.api import WorkspaceMode


def test_a_host_that_isolates_candidates_gives_each_its_own_workspace_and_one_that_cannot_shares_the_root() -> (
    None
):
    assert workspace_mode_for(isolated_candidates=True) is WorkspaceMode.ISOLATED_CHILD
    assert workspace_mode_for(isolated_candidates=False) is WorkspaceMode.EXCLUSIVE_ROOT
