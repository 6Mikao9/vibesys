"""Pure registered-operation contract for guarded evaluation admission reopening."""

from __future__ import annotations

from typing import ClassVar, Literal

from pydantic import model_validator

from vs_core._generated.vibesys.core.v1.contracts_cv import ScopedAdmissionReopenOutcome

from .common import (
    AttemptRef,
    ContinuationId,
    LifecycleClass,
    RequestId,
    ResourceId,
    ScopeReopenNormalization,
)
from .strategy import OperationRequest


class ScopedAdmissionReopen(OperationRequest):
    """Registered run-scoped command targeting one exact parked attempt.

    Its descriptor is inspectable, has no pool, has RevisionAuthority.NONE and
    declares SCOPE_REOPEN normalization. Registration binds a pure normalizer to
    its validated payload. Capacity, retained workspace and session leases must
    be positively reacquired before dispatch. Payload cannot select the new
    admission episode; core supplies that episode through dispatch context.
    """

    kind: Literal["evaluation.scope.reopen"] = "evaluation.scope.reopen"
    lifecycle: Literal[LifecycleClass.IDEMPOTENT_WRITE] = LifecycleClass.IDEMPOTENT_WRITE
    outcome_model: ClassVar[type[ScopedAdmissionReopenOutcome]] = ScopedAdmissionReopenOutcome

    attempt: AttemptRef
    continuation_id: ContinuationId
    park_authority: RequestId
    resolved_cancelled_jobs: tuple[ResourceId, ...]

    @model_validator(mode="after")
    def distinct_resolutions(self) -> ScopedAdmissionReopen:
        """Validate the same canonical resolution contract used by routing."""
        ScopeReopenNormalization(
            attempt=self.attempt,
            continuation_id=self.continuation_id,
            park_authority=self.park_authority,
            resolved_cancelled_jobs=self.resolved_cancelled_jobs,
        )
        return self


__all__ = ["ScopedAdmissionReopen", "ScopedAdmissionReopenOutcome"]
