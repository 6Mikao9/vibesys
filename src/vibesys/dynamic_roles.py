"""The agent roles of a dynamic run on the core path.

They are declared here, outside the legacy orchestration tree, because a role is host wiring:
its capabilities and workspace access name `vs_runtime` values and its prompt is read from a
template file, neither of which the pure policy in `core_policy` may do.

A core turn ends with a typed reply that the search folds. The implementer and the judge
carry the bridged evaluation tool; no other role carries a tool, so none needs an MCP server. Each
role's id is the search's (`IDS.role_id`), so the roles the search names and the roles the
host declares cannot drift apart.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from vibesys.core_roles import (
    CONTINUING_READER,
    MEASURING_READER,
    MEASURING_WRITER,
    READER,
    RoleKind,
    core_role,
)
from vibesys.orchestration.dynamic.core_policy.api import prompts
from vibesys.orchestration.dynamic.search.api import IDS, Role
from vs_prompts.api import TemplateRenderer

if TYPE_CHECKING:
    from vs_runtime.api import AgentRole

_RENDERER = TemplateRenderer(Path(prompts.__file__).parent)


def _role(role: Role, kind: RoleKind) -> AgentRole:
    return core_role(
        _RENDERER,
        role_id=IDS.role_id(role.value).root,
        system_template=f"{role.value}_system.j2",
        kind=kind,
    )


ORCHESTRATOR = _role(Role.PLANNER, READER)
IMPLEMENTER = _role(Role.IMPLEMENTER, MEASURING_WRITER)
JUDGE = _role(Role.JUDGE, MEASURING_READER)
PROFILER = _role(Role.PROFILER, CONTINUING_READER)

CORE_ROLES = (ORCHESTRATOR, IMPLEMENTER, JUDGE, PROFILER)
