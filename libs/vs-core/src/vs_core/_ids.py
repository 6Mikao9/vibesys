"""The identity grammar a strategy uses to name what it proposes.

Core deduplicates a replayed proposal by decision identity and payload digest, so every
identity a strategy allocates must be a pure function of names and counters in its own
state. This module owns that grammar once: a strategy picks a namespace and gets decision,
invocation and session identities that cannot collide with another strategy's in one run.
Attempt and item identities carry no namespace, because a run has one strategy.

Identities hold no whitespace, and an agent-chosen name is free text (spaces, slashes,
non-ASCII), so a name enters an identity only through `component`.
"""

from __future__ import annotations

from dataclasses import dataclass

from vs_core.types.common import (
    AttemptId,
    AttemptRef,
    DecisionId,
    InvocationId,
    ItemId,
    SessionId,
)

_OPERATION_PREFIX = "operation:"
_PLAIN = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._-")


def component(name: str) -> str:
    """An agent-chosen name as one identity component.

    Percent-encoding is injective and keeps the name readable.
    """
    return "".join(
        char if char in _PLAIN else "".join(f"%{byte:02X}" for byte in char.encode())
        for char in name
    )


def attempt_id(subject: str, sequence: int) -> AttemptId:
    """One attempt per scheduled workstream; a continuation is a new sequence."""
    return AttemptId(root=f"attempt:{component(subject)}:{sequence}")


def item_id(work_id: str, sequence: int) -> ItemId:
    """The scientific item an attempt works on."""
    return ItemId(root=f"item:{component(work_id)}:{sequence}")


def attempt_ref(attempt: AttemptId, generation: int) -> AttemptRef:
    """Exact attempt generation."""
    return AttemptRef(attempt_id=attempt, generation=generation)


def decision_of_operation(operation_root: str) -> str | None:
    """Invert core's `operation:<decision>` operation identity, or None if foreign."""
    if operation_root.startswith(_OPERATION_PREFIX):
        return operation_root.removeprefix(_OPERATION_PREFIX)
    return None


class InvalidNamespaceError(ValueError):
    """A namespace that is not one plain component, or that collides with core's own."""

    def __init__(self, namespace: str) -> None:
        super().__init__(
            f"id namespace {namespace!r} must be one plain component other than "
            "'operation', which names core's own operation identities"
        )


@dataclass(frozen=True)
class IdScheme:
    """The decision, invocation and session identities of one strategy namespace.

    Only decision identities carry the namespace: invocation and session identities already
    name their role, and a run has one strategy. Every subject argument is raw text; the
    scheme encodes it.
    """

    namespace: str

    def __post_init__(self) -> None:
        if self.namespace != component(self.namespace) or self.namespace == "operation":
            raise InvalidNamespaceError(self.namespace)

    def decision_id(self, kind: str, subject: str, ordinal: int = 0) -> DecisionId:
        """Name one decision by its kind, owning subject and ordinal."""
        return DecisionId(root=f"{self.namespace}:{kind}:{component(subject)}:{ordinal}")

    def invocation_id(self, role: str, subject: str, serial: int) -> InvocationId:
        """One logical invocation, distinct for every correction and resume."""
        return InvocationId(root=f"inv:{role}:{component(subject)}:{serial}")

    def session_id(self, role: str, subject: str) -> SessionId:
        """Conversation identity: stable per subject so continuations reuse a session."""
        return SessionId(root=f"session:{role}:{component(subject)}")
