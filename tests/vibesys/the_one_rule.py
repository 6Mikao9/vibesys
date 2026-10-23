"""Who wins, stated from vs-core's one rule, for tests that restate it as their oracle.

A candidate is better than a base, a base leads among candidates, only by what
`vs_core.api.judge` and `dominance` say. A test that checks which candidate a search adopted
restates that here, from the rule alone, so it fails when the search decides some other way and
never when the rule moves: a flat tolerance written into a test is a second rule.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_core.api import ComparisonVerdict, Runs, judge

if TYPE_CHECKING:
    from collections.abc import Sequence


def beats(value: float, base: float, *, declared: float = 0.0) -> bool:
    """Whether one run of ``value`` is better than ``base`` on a ``max`` objective, by the rule.

    ``declared`` is the relative noise the task declares for the objective; 0 declares none, and
    the rule then states its default.
    """
    verdict = judge(
        Runs(mean=value, count=1), Runs(mean=base, count=1), direction="max", declared=declared
    ).verdict
    return verdict is ComparisonVerdict.BETTER


def lead_of[K](entries: Sequence[tuple[K, float]], *, declared: float = 0.0) -> K:
    """The key that leads, in the order the candidates were made.

    A later candidate takes over only when it beats the leader by the rule, so a gain within the
    noise keeps the earlier one.
    """
    (led, value), *rest = entries
    for key, found in rest:
        if beats(found, value, declared=declared):
            led, value = key, found
    return led
