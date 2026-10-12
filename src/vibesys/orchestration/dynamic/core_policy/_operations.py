"""The codec registrations of the dynamic search's operations, for the launch to assemble."""

from vibesys.orchestration.dynamic.search.api import OPERATIONS
from vs_core.api import OperationRegistration, OperationRegistry


def dynamic_operation_registrations() -> tuple[OperationRegistration, ...]:
    """Codec registrations the launch assembles beside other libraries' operations."""
    return OPERATIONS.registrations()


def dynamic_operation_registry() -> OperationRegistry:
    """A closed codec holding only the dynamic search's operations."""
    return OPERATIONS.registry()
