"""Retry as values: an attempt says ``Again`` or gives its result; the policy bounds the tries."""

from vs_core.types.agents import Backoff
from vs_core.types.common import Value


class Again[R](Value):
    """Try again, because ``reason``."""

    reason: R


class Tries[R](Value):
    """What an attempt is told: which try this is, and why the earlier ones failed."""

    number: int
    reasons: tuple[R, ...]


class GaveUp[R](Value):
    """Every try asked for another; ``reasons`` are theirs, in order."""

    reasons: tuple[R, ...]


class RetryPolicy(Value):
    """At most ``max_tries`` attempts, waiting ``backoff`` (on the run clock) between them."""

    max_tries: int
    backoff: Backoff | None = None
