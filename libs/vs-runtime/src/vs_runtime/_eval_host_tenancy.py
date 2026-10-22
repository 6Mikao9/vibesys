"""Whether the host that runs a trusted evaluation is shared or exclusive.

The environment that opens the run declares the default. A task may override it.
Core never branches on a backend or environment name to decide this.
"""

from __future__ import annotations

from enum import StrEnum


class EvalHostTenancy(StrEnum):
    """Whether other work can contend with a trusted evaluation on its host."""

    SHARED = "shared"
    """Other processes may run on the evaluation host while a measurement runs."""
    EXCLUSIVE = "exclusive"
    """The environment guarantees the evaluation holds its node alone."""


def declared_eval_host_tenancy(*, local_host: bool, exclusive_node: bool) -> EvalHostTenancy:
    """The default an environment declares from the two facts it actually has.

    A local host is shared: the machine also runs the operator's other work.
    That wins over an exclusive-node claim, so a host launch cannot look exclusive.
    An environment that guarantees an exclusive node, and is not a local host,
    is exclusive. Anything else is shared, because it does not guarantee the node.
    """
    if local_host or not exclusive_node:
        return EvalHostTenancy.SHARED
    return EvalHostTenancy.EXCLUSIVE


def resolve_eval_host_tenancy(
    default: EvalHostTenancy, override: EvalHostTenancy | None
) -> EvalHostTenancy:
    """Use the task override when the manifest sets one, otherwise the environment default."""
    if override is None:
        return default
    return override
