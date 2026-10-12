"""The codec registrations of the multi search's operations, for the launch to assemble."""

from vibesys.orchestration.multi.search.api import OPERATIONS
from vs_core.api import OperationRegistration, OperationRegistry


def multi_operation_registrations() -> tuple[OperationRegistration, ...]:
    """Codec registrations the launch assembles beside other libraries' operations."""
    return OPERATIONS.registrations()


def multi_operation_registry() -> OperationRegistry:
    """A closed codec holding only the multi search's operations."""
    return OPERATIONS.registry()
