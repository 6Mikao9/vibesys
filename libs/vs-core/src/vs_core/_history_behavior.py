"""Pure projections and source-observation checks of generated evaluation history."""

from __future__ import annotations

from typing import TYPE_CHECKING

from vs_core._corevalue import ContractValidationError
from vs_core._generated.vibesys.core.v1.leaves_cv import EvaluationHistoryCursor
from vs_core._generated.vibesys.core.v1.vocabulary_cv import ObservationStatus

if TYPE_CHECKING:
    from vs_core._generated.vibesys.core.v1.common_cv import RequestId
    from vs_core._generated.vibesys.core.v1.leaves_cv import Observation


class TerminalFacts:
    """Check the external source of already normalized scientific facts."""

    __slots__ = ()

    def validate_observation(self, observation: Observation) -> None:
        """Scientific facts require an accepted, conclusive terminal observation."""
        if (
            not observation.accepted
            or not observation.terminal
            or observation.status in (ObservationStatus.PENDING, ObservationStatus.UNKNOWN)
        ):
            raise ContractValidationError(
                "evaluation_result", "requires accepted conclusive terminal observation"
            )


class HistoryCursor:
    """Project the last prepared identity without claiming terminal coverage."""

    __slots__ = ()
    covered_submissions: tuple[RequestId, ...]

    @property
    def cursor(self) -> EvaluationHistoryCursor:
        """The exact last prepared submission, or the empty cursor."""
        if not self.covered_submissions:
            return EvaluationHistoryCursor()
        return EvaluationHistoryCursor(
            ordinal=len(self.covered_submissions), submission_id=self.covered_submissions[-1]
        )
