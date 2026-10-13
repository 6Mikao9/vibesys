"""The agent roles of an evolve run on the core path.

The search declares each role once (`orchestration/evolve/search/_roles`): the template, the
reply, the workspace, the time and the corrections. A role here is what only the host can add:
the system prompt read from a template file, the capabilities the provider must have and the
tools the agent is given. Each role's id comes from the search's id scheme, so the roles the
search asks and the roles the host declares cannot drift apart.

The mutator edits its candidate and, like the judge that reads it, carries the bridged evaluation
tool; the profiler only reads the candidate and analyses captures with the stateless profiler
tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibesys.core_roles import (
    MEASURING_READER,
    MEASURING_WRITER,
    RoleKind,
    core_role,
)
from vibesys.orchestration.evolve.core_policy.api import prompts
from vibesys.orchestration.evolve.search import api as search
from vs_prompts.api import TemplateRenderer
from vs_runtime.api import AgentCapability, AgentTool, WorkspaceAccess

if TYPE_CHECKING:
    from vs_core.api.search import Role
    from vs_runtime.api import AgentRole

_RENDERER = TemplateRenderer(Path(prompts.__file__).parent)

# The profiler analyses captures with the profiler's tool server, which holds no run state (so it
# is no second writer beside core).
PROFILER_KIND = RoleKind(
    WorkspaceAccess.READ_ONLY,
    frozenset({AgentCapability.MCP_SERVERS}),
    (AgentTool(id="profiler", stateless=True),),
)


def _role(role: Role, kind: RoleKind) -> AgentRole:
    return core_role(
        _RENDERER,
        role_id=search.evolve.ids.role_id(role.name).root,
        system_template=f"{role.name}_system.j2",
        kind=kind,
    )


MUTATOR = _role(search.MUTATOR, MEASURING_WRITER)
JUDGE = _role(search.JUDGE, MEASURING_READER)
PROFILER = _role(search.PROFILER, PROFILER_KIND)

CORE_ROLES = (MUTATOR, JUDGE, PROFILER)
