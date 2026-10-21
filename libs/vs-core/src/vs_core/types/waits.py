"""What a waiting entity waits on, recorded by the transition that made it wait."""

from __future__ import annotations

from vs_core._generated.vibesys.core.v1.leaves_cv import (
    WaitOn,
    WaitSubject,
)
from vs_core._generated.vibesys.core.v1.vocabulary_cv import (
    WaitKind,
)

__all__ = ["WaitKind", "WaitOn", "WaitSubject"]
