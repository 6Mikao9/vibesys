"""The codec registrations of the issue-queue search's operations, for the launch to assemble."""

from vibesys.orchestration.issue_queue.search.api import (
    INTERPRET_KIND,
    OPERATIONS,
    RENDER_KIND,
    VERIFY_KIND,
)
from vs_core.api import OperationRegistration, OperationRegistry
from vs_issue_tracker.api import board_operation_registrations


def plain_operation_registrations() -> tuple[OperationRegistration, ...]:
    """Every operation the search's runs record, in the order its declaration lists them.

    Render comes first (the agent pipeline's), then the board's three and the evidence decoder
    the search requires, then the optional parent verification the kit registers beside them.
    """
    by_kind = {item.descriptor.kind: item for item in OPERATIONS.registrations()}
    return (
        by_kind[RENDER_KIND],
        *board_operation_registrations(),
        by_kind[INTERPRET_KIND],
        by_kind[VERIFY_KIND],
    )


def plain_operation_registry() -> OperationRegistry:
    """A closed codec holding only the issue-queue search's operations."""
    return OperationRegistry(plain_operation_registrations())
