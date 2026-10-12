"""Evolve's identities: the shared core grammar under the `evo` namespace, and its child keys.

A search keeps its concurrent work in `Children` keyed by text, and the key order is the order
the search folds finished work in. A frame's slots are numbered, so their keys are zero-padded
to sort in slot order. The key is also the subject the agent pipeline names a slot's sessions
by, so every identity is a pure function of the search's own counters.
"""

from __future__ import annotations

from vs_core.api.search import IdScheme

# The role prefix matches the role ids in `vibesys.evolve_roles`.
IDS = IdScheme("evo", role_prefix="evolve-")

BOOTSTRAP_PREFIX = "bootstrap."


def bootstrap_key(number: int) -> str:
    """The key of the ``number``-th cold-start attempt (numbered from 1)."""
    return f"{BOOTSTRAP_PREFIX}{number:06d}"


def child_key(generation: int, slot: int) -> str:
    """The key of the ``slot``-th child (from 1) of a generation."""
    return f"g{generation:06d}.c{slot:06d}"
