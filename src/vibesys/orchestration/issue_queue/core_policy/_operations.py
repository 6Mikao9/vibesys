"""The codec registrations of the issue-queue search's operations, for the launch to assemble."""

from vibesys.orchestration.issue_queue.search.api import OPERATIONS
from vs_core.api import OperationRegistration, OperationRegistry
from vs_issue_tracker.api import board_operation_registrations


def plain_operation_registrations() -> tuple[OperationRegistration, ...]:
    """Every operation the search's runs record: the kit's render, verify and interpret, then the board's."""
    render, *unused = OPERATIONS.registrations()
    return (render, *board_operation_registrations(), *unused)


def plain_operation_registry() -> OperationRegistry:
    """A closed codec holding only the issue-queue search's operations."""
    return OperationRegistry(plain_operation_registrations())
