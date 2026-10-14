"""The keys of dynamic's concurrent flows: each planning call and each workstream has its own.

The key order is the order the search folds finished work in: workstreams in the order they
were scheduled.
"""

from __future__ import annotations

from vs_core.api.search import component

PLANNING_PREFIX = "plan."


def planning_key(call: int) -> str:
    """The key of the ``call``-th planning call (calls are numbered from 1)."""
    return f"{PLANNING_PREFIX}{call}"


def workstream_key(sequence: int, work_id: str) -> str:
    """The key of a workstream: its schedule position first, so keys sort in schedule order."""
    return f"{sequence:06d}.{component(work_id)}"
