"""Dynamic's identities: the shared core grammar under the `dyn` namespace.

The grammar lives in `vs_core.api` (`IdScheme`); this module fixes dynamic's namespace and
role-id convention. Every identity is a pure function of counters and names in the strategy
state, so replaying `decide` after a restart re-proposes the same canonical identities.
"""

from __future__ import annotations

from vs_core.api import (
    IdScheme,
    attempt_id,
    attempt_ref,
    component,
    decision_of_operation,
    item_id,
)

__all__ = [
    "IDS",
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

# The role prefix matches the role ids in `vibesys.dynamic_roles`.
IDS = IdScheme("dyn", role_prefix="dynamic-")
decision_id = IDS.decision_id
invocation_id = IDS.invocation_id
session_id = IDS.session_id
role_id = IDS.role_id
