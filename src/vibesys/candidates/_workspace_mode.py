"""Where a search's candidates work: the one rule from the run's capabilities to a workspace mode.

A host that can open an isolated candidate sandbox gives every candidate its own workspace and
lets them run side by side. A host that cannot (no container of its own for the agent, or an agent
session borrowed from the run) lets candidates take turns in the run's own root, one at a time.
"""

from vs_core.api import WorkspaceMode


def workspace_mode_for(*, isolated_candidates: bool) -> WorkspaceMode:
    """The mode candidate workspaces are opened in on a host that does or does not isolate them."""
    return WorkspaceMode.ISOLATED_CHILD if isolated_candidates else WorkspaceMode.EXCLUSIVE_ROOT
