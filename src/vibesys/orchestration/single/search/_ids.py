"""Single's identities: the shared core grammar under the `sgl` namespace, and its child keys.

A search keeps its concurrent work in `Children` keyed by text. The input measurement, the design
of the next hypothesis and the open round have their own keys; a round's key carries its number so
its decisions, its agent conversation and its attempt never collide with an earlier round's.
"""

from __future__ import annotations

from vs_core.api.search import IdScheme

# The role prefix matches the role ids in `vibesys.single_roles`.
IDS = IdScheme("sgl", role_prefix="single-")

BASELINE_KEY = "baseline"
DESIGN_KEY = "design"


def round_key(number: int) -> str:
    """The key of round ``number`` (rounds are numbered from 1)."""
    return f"round.{number}"
