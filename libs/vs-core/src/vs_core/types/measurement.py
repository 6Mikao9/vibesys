"""Measured samples and the comparison of a candidate with its base.

One *measurement* is the set of conclusive runs of one measured input under one evaluation.
Each run is a ``Sample``, which cannot exist without the commit it measured and the digest of
what the evaluation read there. Samples of one input count together: nothing is picked among
them and nothing is refused for there being several (`vs_core._comparison`).
"""

from __future__ import annotations

from vs_core._generated.vibesys.core.v1.leaves_cv import (
    BaseClaim,
    BaseRef,
    Comparison,
    ComparisonSettled,
    CoreRun,
    Direction,
    Finite,
    Reading,
    Runaway,
    Sample,
    StatedComparison,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    ComparisonVerdict,
    EffectVerdict,
)

__all__ = [
    "BaseClaim",
    "BaseRef",
    "Comparison",
    "ComparisonSettled",
    "ComparisonVerdict",
    "CoreRun",
    "Direction",
    "EffectVerdict",
    "Finite",
    "Reading",
    "Runaway",
    "Sample",
    "StatedComparison",
]
