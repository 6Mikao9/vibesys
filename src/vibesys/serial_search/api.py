"""Public surface of the policy shared by the serial hypothesis-search strategies."""

from pathlib import Path

from vibesys.serial_search import prompts as _prompts
from vibesys.serial_search._attribution import (
    OUTPUT_ARGUMENT,
    AttributionConfig,
    AttributionFailure,
    AttributionKind,
    attribution_reason,
    bottlenecks_of,
    capture_failure,
    focus_of,
    focus_policy,
    fold_capture,
    observe,
    record_round,
)
from vibesys.serial_search._failure import (
    Failure,
    FailureKind,
    Role,
    Unmeasured,
    failure_of,
)
from vibesys.serial_search._feedback import describe, describe_unmeasured
from vibesys.serial_search._giveup import GiveUp, giveup_reason
from vibesys.serial_search._ledger import OfficialReason, StoredLedger, official_reason
from vibesys.serial_search._ledger import decode as decode_ledger
from vibesys.serial_search._ledger import encode as encode_ledger
from vibesys.serial_search._plan_view import (
    Bottleneck,
    FrontierRow,
    LastProfile,
    PendingClaim,
    PlanEvidence,
    RoundRow,
    plan_evidence,
)
from vibesys.serial_search._planning import (
    PLAN_PREFIX,
    Asking,
    GaveUp,
    Planned,
    Planning,
    PlanWorld,
)
from vibesys.serial_search._planning import begin as begin_planning
from vibesys.serial_search._planning import update as update_planning
from vibesys.serial_search._plans import (
    PlanCorrection,
    PlanProblem,
    ProblemKind,
    SerialTemplate,
    accept_plan,
    base_commit,
)

PROMPTS_DIR = Path(_prompts.__file__).parent
"""Where the shared `plan.j2` lives; a strategy lists it as a fallback root of its core policy."""

__all__ = [
    "OUTPUT_ARGUMENT",
    "PLAN_PREFIX",
    "PROMPTS_DIR",
    "Asking",
    "AttributionConfig",
    "AttributionFailure",
    "AttributionKind",
    "Bottleneck",
    "Failure",
    "FailureKind",
    "FrontierRow",
    "GaveUp",
    "GiveUp",
    "LastProfile",
    "OfficialReason",
    "PendingClaim",
    "PlanCorrection",
    "PlanEvidence",
    "PlanProblem",
    "PlanWorld",
    "Planned",
    "Planning",
    "ProblemKind",
    "Role",
    "RoundRow",
    "SerialTemplate",
    "StoredLedger",
    "Unmeasured",
    "accept_plan",
    "attribution_reason",
    "base_commit",
    "begin_planning",
    "bottlenecks_of",
    "capture_failure",
    "decode_ledger",
    "describe",
    "describe_unmeasured",
    "encode_ledger",
    "failure_of",
    "focus_of",
    "focus_policy",
    "fold_capture",
    "giveup_reason",
    "observe",
    "official_reason",
    "plan_evidence",
    "record_round",
    "update_planning",
]
