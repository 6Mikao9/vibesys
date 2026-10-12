"""Scratch space of one `decide` call and the core value builders every strategy needs.

`Draft` accumulates ordered decisions and the evolving strategy state inside a single pure
call; nothing escapes it except the final proposal. The builders turn strategy intent into
core `Scope`, `Operation`, `RequestTurn` and `WorkspaceRef` values using only the `CoreView`
and values the caller passes, so identical inputs always propose byte-identical decisions.
They carry the protocol rules of the values they build (for example that a session is always
declared as reusable) and no policy: every deadline, role and identity is an argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Self

from vs_core.types.common import (
    AttemptId,
    DecisionId,
    InvocationId,
    RevisionRef,
    SchemaRef,
    Scope,
    Value,
    WorkspaceMode,
    WorkspaceRef,
)
from vs_core.types.sessions import Access, SessionSpec, TurnSpec
from vs_core.types.strategy import Command, Operation, OperationRequest, RequestTurn

if TYPE_CHECKING:
    from vs_core._ids import IdScheme
    from vs_core._turn_chain import TurnChain
    from vs_core.types.kernel import CoreView


@dataclass(kw_only=True)
class Draft[S: Value]:
    """The state and decisions one `decide` call is building."""

    view: CoreView
    state: S
    decisions: list[Command] = field(default_factory=list)
    wake_at: float | None = None
    """Earliest run-clock time a held-back turn becomes due."""

    def emit(self, decision: Command) -> None:
        """Append one decision in proposal order."""
        self.decisions.append(decision)

    def due[C: Value](self, turn: TurnChain[C]) -> bool:
        """Whether the turn may be asked now; otherwise note when to be asked again."""
        until = turn.ask_not_before
        if until is None or self.view.run.now_at >= until:
            return True
        self.wake_at = until if self.wake_at is None else min(self.wake_at, until)
        return False

    def update(self, **fields: object) -> Self:
        """Replace state fields without mutating the previous state."""
        self.state = self.state.model_copy(update=fields)
        return self


def run_scope(view: CoreView) -> Scope:
    """The run's own scope at its current generation."""
    return Scope(owner=view.run.run_id, generation=view.run.generation)


def attempt_scope(view: CoreView, attempt: AttemptId, generation: int) -> Scope:
    """An attempt's scope; core reports the live generation, a new attempt starts at 0."""
    live = next((item for item in view.attempts if item.attempt_id == attempt), None)
    return Scope(owner=attempt, generation=generation if live is None else live.generation)


def declared_operation(
    identifier: DecisionId,
    scope: Scope,
    request: OperationRequest,
) -> Operation:
    """A declared operation; the caller waits on it with ``Wait(operation, Within(seconds))``."""
    return Operation(decision_id=identifier, scope=scope, request=request)


def workspace_for(scope: Scope, revision: RevisionRef, mode: WorkspaceMode) -> WorkspaceRef:
    """The exact revision a turn works on and how it may touch it."""
    return WorkspaceRef(scope=scope, revision=revision, mode=mode)


def session_for(scheme: IdScheme, role: str, subject: str, access: Access) -> SessionSpec:
    """The conversation a role speaks in.

    The policy is always "reuse": core and the session executor require every turn of a
    session (first, correction, resume) to carry one spec, and a "fresh" session cannot be
    ensured twice, so a conversation that may continue must declare reuse on its first turn.
    """
    return SessionSpec(
        session_id=scheme.session_id(role, subject),
        role_id=scheme.role_id(role),
        policy="reuse",
        lifetime="owner",
        access=access,
    )


def invocation_for[C: Value](
    scheme: IdScheme, role: str, subject: str, turn: TurnChain[C]
) -> InvocationId:
    """The logical invocation ID of a turn; a resume reuses the ID core authorized."""
    if turn.resume_as is not None:
        return turn.resume_as
    return scheme.invocation_id(role, subject, turn.serial)


def turn_decision_id[C: Value](scheme: IdScheme, subject: str, turn: TurnChain[C]) -> DecisionId:
    """Command ID of one turn request."""
    return scheme.decision_id("turn", subject, turn.serial)


@dataclass(frozen=True)
class TurnShape:
    """Who runs a turn, on what, with which access, and what it must answer with."""

    role: str
    subject: str
    workspace: WorkspaceRef | Scope
    access: Access
    output_schema: SchemaRef


def request_turn[C: Value](
    scheme: IdScheme, shape: TurnShape, turn: TurnChain[C], scope: Scope
) -> RequestTurn:
    """Build the paid, free, correction or resume turn the chain describes."""
    role, subject = shape.role, shape.subject
    return RequestTurn(
        decision_id=turn_decision_id(scheme, subject, turn),
        scope=scope,
        turn=TurnSpec(
            session=session_for(scheme, role, subject, shape.access),
            invocation_id=invocation_for(scheme, role, subject, turn),
            continuation_id=turn.continuation,
            workspace=shape.workspace,
            prompts=turn.prompts,
            output_schema=shape.output_schema,
            tool_policy=turn.tool_policy,
            charge_class=turn.charge,
            # A resume names its continuation, never a predecessor: core rejects both together.
            predecessor=None if turn.charge == "resume" else turn.invocation,
        ),
    )
