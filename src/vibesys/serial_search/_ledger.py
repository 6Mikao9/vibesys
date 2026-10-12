"""The hypothesis ledger as a search keeps it, and the reasons the ledger gives as data.

The ledger (`vibesys.hypothesis.rounds`) is a pair of JSON documents; a search keeps them as one
immutable value and decodes them where it needs the ledger's view. `OfficialReason` is the closed
set of reasons the ledger schedules the official gates for a round.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from vibesys.hypothesis import rounds
from vs_core.api.search import Value

if TYPE_CHECKING:
    from vibesys.hypothesis import HypothesisConfig
    from vibesys.metrics import MetricSpace


class StoredLedger(Value):
    """The two JSON documents of the hypothesis ledger, as the round machine encodes them."""

    search_json: str = ""
    carry_json: str = ""


class OfficialReason(StrEnum):
    """Why the official gates are due after a round's candidate. Closed."""

    FINAL_ROUND = "final_round"
    ORCHESTRATOR_REQUEST = "orchestrator_request"
    CADENCE = "cadence"
    COMPONENT_MEASUREMENT = "profile-guided component measurement"
    """A profile-guided run measures the component it is focused on."""


def decode(stored: StoredLedger, space: MetricSpace) -> rounds.Ledger:
    """The ledger projected onto the run's metric space."""
    return rounds.decode(
        rounds.EncodedLedger(search_json=stored.search_json, carry_json=stored.carry_json), space
    )


def encode(ledger: rounds.Ledger) -> StoredLedger:
    """The ledger as the documents a search keeps."""
    encoded = rounds.encode(ledger)
    return StoredLedger(search_json=encoded.search_json, carry_json=encoded.carry_json)


def official_reason(
    config: HypothesisConfig,
    ledger: rounds.Ledger,
    *,
    round_number: int,
    requested: bool,
    candidate_ready: bool = True,
) -> OfficialReason | None:
    """Why the official gates are due after this round's candidate, or None."""
    reason = rounds.official_reason(
        config,
        ledger,
        round_number=round_number,
        requested=requested,
        candidate_ready=candidate_ready,
    )
    return None if reason is None else OfficialReason(reason)
