"""The codec registrations of the single search's operations, for the launch to assemble."""

from vibesys.orchestration.single.search.api import OPERATIONS
from vs_core.api import OperationRegistration, OperationRegistry


def single_operation_registrations() -> tuple[OperationRegistration, ...]:
    """Codec registrations the launch assembles beside other libraries' operations."""
    return OPERATIONS.registrations()


def single_operation_registry() -> OperationRegistry:
    """A closed codec holding only the single search's operations."""
    return OPERATIONS.registry()
