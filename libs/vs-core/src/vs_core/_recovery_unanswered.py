"""What recovery does with a check that nothing is left to answer.

A recovery check waits on the requests that can move it: its target while that is open, and the
inspections sent about it. Once every one of them has ended and the check is still pending, no
event will ever come, and a target with no limit left to end it would hold admission closed in
silence. Recovery never waits without bound on an answer nobody will give: it settles the check
by what the request is, and the verdict is one of five.

* ``resend``: the request is idempotent (or a pure query) and still open, so it goes out again
  under its own identity, and the live path answers it as it answers any request. An adoption
  is sent again even when its inspection could not be answered: adopting restores the root to
  the selected revision and then verifies it, so it needs no knowledge of the dead host's try.
* ``unknown``: the request's result is discarded whatever it was (a cancel of a job), so an
  answer that will not come is settled, and the check says it was settled as unknown.
* ``terminal``: core ended the request itself, its owner heard so, and it owns nothing.
* ``blocked``: its result matters and nothing can redo it (an owned job, a session turn, an
  effect that already finished and left no proof), or its executor could not be reached, or it
  left a resource nobody proved released. Recovery blocks on that record, and the run ends
  blocked naming it.
* ``lost``: the request is still owed a result and its executor was the host instance that
  dispatched it, which is gone (`executor_lifetime`). No answer can ever come, so core ends the
  record as lost now rather than at its limit, and its owner hears it: a turn is cancelled as a
  lost one and its role asks again, as for a turn that went silent in a live run.

An executor that is the host instance (an agent session and its turns) is the case where waiting
for an answer is hopeless: the instance died with its provider process and its open turn, so a
restarted executor can only say that the turn began and has no recorded result. What the
request means once its instance is gone is a fact core already holds (the host epoch that
dispatched it is not the current one), so it settles at once and never ends unknown: a turn is
lost, an ensure, a cancel or a close holds nothing a new executor could find or release, and an
open write that still has an owner goes out again (all idempotent).

Recovery first reads the target's own facts: one that another request settled is resolved by
them, as a turn its host died in is once the cancellation of the lost turn released it. Only a
target its facts leave unresolved gets a verdict here.

The policy is a pure function of the facts below, so it does not branch on a backend and
recovery applies it in one place.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from .types.common import LifecycleClass
from .types.evaluation import CancelOwnedJob
from .types.records import RequestPhase, RequestRecord
from .types.settlement import AdoptRevision, VerifyAdoption

type Settlement = Literal["resend", "unknown", "terminal", "blocked", "lost"]


class Inspection(StrEnum):
    """What became of the inspection recovery sent about a check's target."""

    NONE = "none"
    """No inspection was sent: the check waited on something else (a child's release)."""
    ANSWERED = "answered"
    """The executor answered, and the answer resolved nothing."""
    UNREACHABLE = "unreachable"
    """It ended at its bound, or at its retries, without an answer."""


def settlement(
    record: RequestRecord,
    *,
    inspection: Inspection,
    owes_release: bool,
    resendable: bool,
    executor_gone: bool,
) -> Settlement:
    """How recovery settles a pending check whose target nothing will answer for.

    *owes_release*: a resource the record left is not proved released. *resendable*: the record
    is still of the episode that may send it again. *executor_gone*: the record's executor is
    the host instance that dispatched it (`executor_lifetime`), and that instance is gone.
    """
    if owes_release:
        return "blocked"
    if record.phase == RequestPhase.BLOCKED:
        # Core ended it at its limit and its owner heard so: no executor fact can change that.
        return "terminal"
    redo = resendable and record.phase in _OPEN
    if redo and isinstance(record.request, AdoptRevision | VerifyAdoption):
        # Restoring the root to the selected revision and then verifying it gives the same
        # result however far the dead host got, so the inspection was only a shortcut.
        return "resend"
    if executor_gone:
        # Whether the new executor was reached says nothing: the answer is gone with the instance.
        return _of_dead_instance(record, redo=redo)
    if inspection is Inspection.UNREACHABLE:
        return "blocked"
    return _by_kind(record, redo=redo)


_OPEN = (RequestPhase.PREPARED, RequestPhase.DISPATCHED, RequestPhase.RECONCILING)


def _of_dead_instance(record: RequestRecord, *, redo: bool) -> Settlement:
    """The verdict for a request whose executor was a host instance that is gone.

    What it did died with the instance, so the outcome follows from the request, not from an
    answer: one that ended holds nothing, an open write with an owner goes out again, and a
    turn that began and returned nothing is lost (never sent again: its journal would only
    report it unknown).
    """
    if record.lifecycle == LifecycleClass.QUERY:
        return "resend" if redo else "unknown"
    if record.phase not in _OPEN:
        return "terminal"
    if record.lifecycle == LifecycleClass.IDEMPOTENT_WRITE and redo:
        return "resend"
    return "lost"


def _by_kind(record: RequestRecord, *, redo: bool) -> Settlement:
    """The verdict for what the request is, once its executor was reached or not needed."""
    if isinstance(record.request, CancelOwnedJob):
        return "unknown"
    if record.lifecycle == LifecycleClass.QUERY:
        # A query starts nothing and owns nothing: ask it again, or take what it said.
        return "resend" if redo else "unknown"
    return "resend" if redo and record.lifecycle == LifecycleClass.IDEMPOTENT_WRITE else "blocked"


__all__ = ["Inspection", "Settlement", "settlement"]
