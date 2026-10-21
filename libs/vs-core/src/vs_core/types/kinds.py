"""The closed sets of work a run can start and of events core reports, named once.

``WorkKind`` is what a request can ask core for, plus the clock wake-up; ``EventKind`` is the
kinds of event a run's flows receive, past tense, one per thing that happened.
"""

from __future__ import annotations

from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    EventKind,
    WorkKind,
)

__all__ = ["EventKind", "WorkKind"]
