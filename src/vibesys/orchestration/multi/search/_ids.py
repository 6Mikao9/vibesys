"""Multi's identities: the shared core grammar under the `mlt` namespace, and its child keys.

A search keeps its concurrent work in `Children` keyed by text. The input measurement, the design
of the next hypothesis and the open round have their own keys; a round's key carries its number so
its decisions, its agent conversations and its attempt never collide with an earlier round's. The
role prefix matches the role ids in `vibesys.multi_roles`.
"""

from __future__ import annotations

from vs_core.api.search import IdScheme

IDS = IdScheme("mlt", role_prefix="multi-")

BASELINE_KEY = "baseline"
DESIGN_KEY = "design"


def round_key(number: int) -> str:
    """The key of round ``number`` (rounds are numbered from 1)."""
    return f"round.{number}"
