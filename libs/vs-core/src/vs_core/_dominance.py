"""The one judgement of a candidate against a base, objective by objective, and the dominance rule.

Everything that decides whether a result beats, matches or loses to a base reads this module:
core's comparison of samples (`vs_core._comparison`), and every decision the product takes on a
measurement (eligibility, fast-forward, merge, regression, the agent's and the planner's views),
which project from the comparison and never judge a reading themselves.

The statistic
-------------

A run reads each objective once. The candidate has ``m`` runs of an objective with mean ``c`` and
the base ``n`` runs with mean ``b``; the ratio is ``r = c / b``. A run's *relative variation* is
its standard deviation over its mean. The runs' mean has a standard error of that variation over
``sqrt(runs)``, and the ratio of two means has, to first order, the standard error::

    noise = |r| * sqrt(v_c**2 / m + v_b**2 / n)

where ``v_c`` and ``v_b`` are each side's relative variation of one run. The candidate gains by
``g = r - 1`` on a ``max`` objective and ``1 - r`` on a ``min`` one, and the objective's verdict
is ``better`` when ``g > VERDICT_NOISES * noise``, ``worse`` when ``g < -VERDICT_NOISES * noise``
and ``within_noise`` otherwise: a difference counts only once it exceeds two standard errors
(about 95% two-sided), so a run-to-run fluctuation is not called a gain or a loss, and more runs
narrow the band. This is the only definition of noise in the system.

A variation that is declared, or assumed by default, is taken as known, and two standard errors
are the 95% band. A variation estimated from a side's own few runs is itself uncertain: with
two or three runs a side, two standard errors would call a gain at equal means about one time in
eight, not one in twenty. Then the band is the two-sided 95% Student-t quantile at the
Welch-Satterthwaite degrees of freedom (rounded down, which is conservative), and the ``noise``
reported is the standard error widened by that quantile over two, so that two of it is always the
band a verdict needs (every reader of a comparison, including the effect verdict of a plan, holds
a difference against ``VERDICT_NOISES`` of the ``noise`` it states).

Where each side's relative variation comes from, in order:

1. The objective's *declared* noise (``TaskObjective.noise``, else the task's ``relative_noise``)
   wins: the operator measured the benchmark and stated how much one run varies.
2. Else the side's own spread, when it holds at least two runs that differ.
3. Else ``DEFAULT_RELATIVE_NOISE``: with one run, or runs that all read alike (a quantized or
   cached benchmark shows no spread, not that there is none), nothing is known, and a tolerance
   of zero would call any difference a gain. The default is stated, never zero.

A base mean of zero has no relative scale: the objective is judged ``within_noise`` when the
candidate's mean is zero too, and ``inconclusive`` otherwise.

Dominance
---------

The verdicts of all of a comparison's objectives give one result (`dominance`): ``worse`` when
some objective is worse, else ``better`` when some objective is better (no objective worse and at
least one better: the candidate *dominates* the base), else ``inconclusive`` when some objective
cannot be judged, else ``within_noise``. A tradeoff (one better, one worse) is ``worse``: it is a
regression on the objective that lost. A *close call* is exactly ``within_noise``: every
objective is undecided, so more runs could still make the candidate dominate. A dominating
result and a regression are decided, whatever the other objectives would say with more runs.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, inf, sqrt
from typing import TYPE_CHECKING

from .types.measurement import ComparisonVerdict, Direction

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = [
    "DEFAULT_RELATIVE_NOISE",
    "VERDICT_NOISES",
    "Judgement",
    "Runs",
    "dominance",
    "judge",
]

_MIN_RUNS_FOR_SPREAD = 2
"""The runs a side needs for a spread of its own."""
VERDICT_NOISES = 2.0
"""How many standard errors a difference must exceed for a verdict."""
_T_975 = (
    12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
    2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
    2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042,
)  # fmt: skip
"""The two-sided 95% Student-t quantile for 1 to 30 degrees of freedom."""
_DEGREES_SLACK = 1e-9
"""Added before rounding Welch's degrees down: degrees that are a whole number in exact arithmetic
can come out a few units in the last place below it, and the floor would drop a whole degree."""
DEFAULT_RELATIVE_NOISE = 0.05
"""The relative variation of one run assumed where nothing declares it or shows it: 5%.

It matches the operator-calibrated variation of the benchmarks the framework runs (5% to 8%),
and with one run a side it asks for a gain of about 14% (two standard errors of the ratio)."""


@dataclass(frozen=True, slots=True)
class Runs:
    """One side's runs of one objective: their mean, how many there are, and their spread."""

    mean: float
    count: int
    spread: float = 0.0
    """The runs' relative standard deviation; 0 for fewer than two runs, a zero mean, or runs
    that all read alike."""

    @classmethod
    def of(cls, values: Sequence[float]) -> Runs:
        """The runs that read ``values`` (at least one)."""
        mean = sum(values) / len(values)
        return cls(mean=mean, count=len(values), spread=_spread(values, mean))

    def variation(self, declared: float) -> tuple[float, float]:
        """The relative variation of one of these runs and the degrees of freedom it rests on.

        Declared, else the spread (``count - 1`` degrees), else the default. A declared or default
        variation is known, which is infinite degrees.
        """
        if declared > 0:
            return declared, inf
        if self.spread > 0:
            return self.spread, self.count - 1
        return DEFAULT_RELATIVE_NOISE, inf


@dataclass(frozen=True, slots=True)
class Judgement:
    """How a candidate's runs of one objective stand against the base's."""

    ratio: float
    """The candidate's mean over the base's; 0 when the base's mean is zero and the candidate's is not."""
    noise: float
    """One standard error of the ratio."""
    verdict: ComparisonVerdict


def judge(candidate: Runs, base: Runs, *, direction: Direction, declared: float = 0.0) -> Judgement:
    """``candidate`` against ``base`` on one objective (module docstring).

    ``declared`` is the objective's declared relative variation of one run, 0 when it declares
    none.
    """
    if base.mean == 0:
        same = candidate.mean == 0
        return Judgement(
            ratio=1.0 if same else 0.0,
            noise=0.0,
            verdict=ComparisonVerdict.WITHIN_NOISE if same else ComparisonVerdict.INCONCLUSIVE,
        )
    ratio = candidate.mean / base.mean
    mine, free_mine = candidate.variation(declared)
    theirs, free_theirs = base.variation(declared)
    parts = ((mine**2 / candidate.count, free_mine), (theirs**2 / base.count, free_theirs))
    error = abs(ratio) * sqrt(sum(part for part, _ in parts))
    noise = error * _critical(parts) / VERDICT_NOISES
    gain = ratio - 1 if direction == "max" else 1 - ratio
    if gain > VERDICT_NOISES * noise:
        verdict = ComparisonVerdict.BETTER
    elif gain < -VERDICT_NOISES * noise:
        verdict = ComparisonVerdict.WORSE
    else:
        verdict = ComparisonVerdict.WITHIN_NOISE
    return Judgement(ratio=ratio, noise=noise, verdict=verdict)


def _critical(parts: tuple[tuple[float, float], tuple[float, float]]) -> float:
    """How many standard errors the 95% band is: two for known variations, else Student-t.

    ``parts`` are each side's variance of its mean and the degrees of freedom of the variation
    behind it (infinite when known). The Welch-Satterthwaite degrees are those of the sum.
    """
    estimated = [(part, free) for part, free in parts if free != inf]
    if not estimated:
        return VERDICT_NOISES
    total = sum(part for part, _ in parts)
    degrees = total**2 / sum(part**2 / free for part, free in estimated)
    whole = max(floor(degrees + _DEGREES_SLACK), 1)
    return _T_975[min(whole, len(_T_975)) - 1]


def dominance(verdicts: Iterable[ComparisonVerdict]) -> ComparisonVerdict:
    """The one result over a comparison's objective verdicts (module docstring).

    ``BETTER`` is dominance: no objective worse and at least one better. No objective at all
    gives nothing to compare, which is ``INCONCLUSIVE``.
    """
    found = set(verdicts)
    if ComparisonVerdict.WORSE in found:
        return ComparisonVerdict.WORSE
    if not found or ComparisonVerdict.INCONCLUSIVE in found:
        return ComparisonVerdict.INCONCLUSIVE
    if ComparisonVerdict.BETTER in found:
        return ComparisonVerdict.BETTER
    return ComparisonVerdict.WITHIN_NOISE


def _spread(values: Sequence[float], mean: float) -> float:
    """The runs' relative standard deviation: exactly 0 for runs that read alike.

    A float mean of identical values can differ from them in the last bit, which would show a
    spread of about 1e-17 where the runs showed none.
    """
    if len(values) < _MIN_RUNS_FOR_SPREAD or mean == 0 or min(values) == max(values):
        return 0.0
    squares = sum(((x - mean) / mean) ** 2 for x in values)
    return sqrt(squares / (len(values) - 1))
