"""Run-level policy: which candidate wins, how adoption is proposed and how the run ends.

The winner is the best winner-eligible retained candidate by the configured metric space, else
the trusted baseline. Adoption is proposed once, and the run finishes only after core reports
the adoption result.

A run ends in one of three typed ways, so that success always means the operator has a trusted,
measured result to use:

- ``adopted``: a retained, accuracy-verified candidate beat the input (success);
- ``no improvement``: no candidate did, but the input was measured and trusted, so the operator
  keeps it (success);
- ``no trusted result``: the input failed its measurement and no trusted candidate was found,
  so there is nothing to keep (failure).

The search decides *when* selection begins (it knows what is in flight); this module decides
what is chosen, proposed and finished with. ``Adopting`` is the one phase of the machine: the
proposal is out and its result is awaited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from vibesys.candidates.config import CandidateConfig
from vibesys.candidates.settlement import measurement
from vibesys.hypothesis import RunEnding
from vibesys.metrics import MetricRow
from vs_core.api import (
    THIS_RUN,
    AdoptionFailed,
    AdoptionFinished,
    AdoptionResult,
    DecisionId,
    Failed,
    Finish,
    ObservationStatus,
    ProposeWinner,
    RetainedCandidate,
    RevisionRef,
    RunResultProposal,
    RunView,
    Selection,
    SettlementId,
    TrustedBaseline,
    UnexpectedEvent,
    Value,
    Wait,
    Within,
)

if TYPE_CHECKING:
    from vs_core.api import Decision, Event


PROPOSE = DecisionId(root="propose")
FINISH = DecisionId(root="finish")


class Winner(Value):
    """The retained candidate proposed for adoption, or the trusted baseline."""

    settlement: SettlementId | None
    revision: RevisionRef
    hypothesis_id: str | None


@dataclass(frozen=True)
class EligibleRound:
    """One finished, winner-eligible round of a search."""

    hypothesis_id: str
    sequence: int
    settlement: SettlementId
    candidate: RevisionRef
    metrics: tuple[MetricRow, ...]


class Adopting(Value):
    """The proposal is with core; its result decides whether the winner is kept."""

    kind: Literal["adopting"] = "adopting"
    winner: Winner


def ending_reason(ending: RunEnding, detail: str) -> str:
    """The reason a run result carries."""
    return f"{ending.value}: {detail}"


def until_deadline(view: RunView) -> Within:
    """The wait that lasts as long as the run: for work whose own duration nothing bounds."""
    return Within(limit=max(view.deadline_at - view.now_at, 0.0))


def choose_winner(
    rounds: tuple[EligibleRound, ...], config: CandidateConfig, baseline: RevisionRef
) -> Winner:
    """The best eligible candidate by the metric space; earlier rounds win ties."""
    points = sorted(rounds, key=lambda item: item.sequence)
    best = config.metric_space.best(
        points, lambda item: measurement(item.metrics[0] if item.metrics else None)
    )
    if best is None and points and not any(item.metrics for item in points):
        best = points[-1]  # no benchmark configured: the latest eligible candidate
    if best is None:
        return Winner(settlement=None, revision=baseline, hypothesis_id=None)
    return Winner(
        settlement=best.settlement, revision=best.candidate, hypothesis_id=best.hypothesis_id
    )


def selection_of(winner: Winner) -> Selection:
    """The core selection a winner proposes for adoption."""
    if winner.settlement is None:
        return TrustedBaseline(revision=winner.revision)
    return RetainedCandidate(settlement_id=winner.settlement, revision=winner.revision)


def begin(winner: Winner, view: RunView) -> tuple[Adopting, list[Decision]]:
    """Propose the winner for adoption, once."""
    proposal = ProposeWinner(
        decision_id=PROPOSE, scope=THIS_RUN, selection=selection_of(winner)
    )
    return Adopting(winner=winner), [Wait(request=proposal, until=until_deadline(view))]


def update(adopting: Adopting, event: Event) -> Winner | None:
    """The winner core adopted, or None when the adoption ended without one.

    Core refuses a proposal (``Failed``), publishes ``AdoptionFailed`` when the restore or its
    verification failed for good, and otherwise reports the adoption result.
    """
    match event:
        case AdoptionFinished(result=AdoptionResult(observation=seen)):
            return adopting.winner if seen.status is ObservationStatus.SUCCEEDED else None
        case AdoptionFinished(result=AdoptionFailed()) | Failed():
            return None
        case _:
            raise UnexpectedEvent(adopting, event)


def finish(winner: Winner | None, *, stopping: bool, no_winner_reason: str) -> Finish:
    """End the run after the adoption result: success with a winner, else failure.

    An operator stop ends it ``cancelled`` either way.
    """
    if winner is None:
        result = RunResultProposal(
            outcome="cancelled" if stopping else "failure", reason=no_winner_reason
        )
    else:
        ending = RunEnding.NO_IMPROVEMENT if winner.hypothesis_id is None else RunEnding.ADOPTED
        result = RunResultProposal(
            outcome="cancelled" if stopping else "success",
            reason=ending_reason(ending, winner.hypothesis_id or "kept the trusted input"),
            selection=selection_of(winner),
        )
    return Finish(decision_id=FINISH, scope=THIS_RUN, mode="drain", result=result)
