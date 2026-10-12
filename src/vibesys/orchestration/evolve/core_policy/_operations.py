"""The codec registrations of the evolve search's operations, for the launch to assemble."""

from vibesys.orchestration.evolve.search.api import OPERATIONS
from vs_core.api import OperationRegistration, OperationRegistry


def evolve_operation_registrations() -> tuple[OperationRegistration, ...]:
    """Codec registrations the launch assembles beside other libraries' operations."""
    return OPERATIONS.registrations()


def evolve_operation_registry() -> OperationRegistry:
    """A closed codec holding only the evolve search's operations."""
    return OPERATIONS.registry()
