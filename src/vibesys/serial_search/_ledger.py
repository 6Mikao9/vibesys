"""The hypothesis ledger as a search carries it between rounds: two JSON strings.

`vibesys.hypothesis.rounds` works on decoded, mutable-looking objects; a round's snapshot and a
finished round's result are frozen values, so the ledger travels in its encoded form and each
round decodes it, applies its transitions and encodes the result.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from vibesys.hypothesis import rounds
from vs_core.api.search import Value

if TYPE_CHECKING:
    from vibesys.metrics import MetricSpace


class LedgerJson(Value):
    """The persisted form of a hypothesis ledger. Empty text is a new run."""

    search_json: str = ""
    carry_json: str = ""

    @classmethod
    def of(cls, ledger: rounds.Ledger) -> LedgerJson:
        """The persisted form of ``ledger``."""
        encoded = rounds.encode(ledger)
        return cls(search_json=encoded.search_json, carry_json=encoded.carry_json)

    def decode(self, space: MetricSpace | None = None) -> rounds.Ledger:
        """The ledger, projected onto the task's metric space when one is given."""
        encoded = rounds.EncodedLedger(search_json=self.search_json, carry_json=self.carry_json)
        return rounds.decode(encoded, space)
