"""Dynamic's identities: the shared core grammar under the `dyn` namespace, and its child keys.

A search keeps its concurrent work in `Children` keyed by text. The baseline measurement, each
planning call and each workstream have their own key, and the key order is the order the search
folds finished work in: workstreams first, in the order they were scheduled.
"""

from __future__ import annotations

from vs_core.api import IdScheme, component

# The role prefix matches the role ids in `vibesys.dynamic_roles`.
IDS = IdScheme("dyn", role_prefix="dynamic-")

BASELINE_KEY = "baseline"
PLANNING_PREFIX = "plan."


def planning_key(call: int) -> str:
    """The key of the ``call``-th planning call (calls are numbered from 1)."""
    return f"{PLANNING_PREFIX}{call}"


def workstream_key(sequence: int, work_id: str) -> str:
    """The key of a workstream: its schedule position first, so keys sort in schedule order."""
    return f"{sequence:06d}.{component(work_id)}"
