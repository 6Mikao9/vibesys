"""Plugin registration for the evolutionary search: the core path.

Every entrypoint resolves `evolve` to this registration. The run is a vs-core flow driven by the
vs-runtime shell; `evolve_core` composes it.
"""

from vibesys.evolve_core import evolve_core_registration

REGISTRATION = evolve_core_registration()
PLUGIN = REGISTRATION.plugin

__all__ = ["PLUGIN", "REGISTRATION"]
