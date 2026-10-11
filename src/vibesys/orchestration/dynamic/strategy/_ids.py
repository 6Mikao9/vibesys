"""Dynamic's identities: the shared core grammar under the `dyn` namespace.

The grammar lives in `vs_core.api` (`IdScheme`); this module fixes dynamic's namespace and
role-id convention. Every identity is a pure function of counters and names in the strategy
state, so replaying `decide` after a restart re-proposes the same canonical identities.
"""

from __future__ import annotations

from vs_core.api import (
    IdScheme,
    RoleId,
    attempt_id,
    attempt_ref,
    component,
    decision_of_operation,
    item_id,
)

__all__ = [
    "attempt_id",
    "attempt_ref",
    "component",
    "decision_id",
    "decision_of_operation",
    "invocation_id",
    "item_id",
    "role_id",
    "session_id",
]

_SCHEME = IdScheme("dyn")
decision_id = _SCHEME.decision_id
invocation_id = _SCHEME.invocation_id
session_id = _SCHEME.session_id


def role_id(role: str) -> RoleId:
    """Agent role identity, matching the role ids in `vibesys.dynamic_roles`."""
    return RoleId(root=f"dynamic-{role}")
