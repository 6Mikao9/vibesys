"""What the whole-loop harness needs to know about a strategy, keyed by plugin id.

The scenario harness drives a strategy through the production host: it builds the CLI request,
decodes the committed envelope and checks liveness. Everything in it that names a concrete
strategy lives here, so a ported strategy registers one `LoopSpec` and reuses the harness, the
loop invariants and the chaos driver.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from vibesys.orchestration.dynamic.core_policy.api import (
    dynamic_operation_registry,
)
from vibesys.orchestration.dynamic.search.api import (
    DynamicState,
    PromptContext,
)
from vs_core.api import Piped, RunEnvelope

if TYPE_CHECKING:
    from collections.abc import Callable

    from vs_core.api import CoreState


@dataclass(frozen=True)
class LoopSpec:
    """One strategy as the harness sees it."""

    outer_loop: str
    """The CLI selection (`--outer-loop`) that runs the strategy."""
    decode_core: Callable[[str], CoreState]
    """Core's state out of the committed envelope's JSON, through the strategy's own codec."""


def _dynamic_core(envelope: str) -> CoreState:
    codec = dynamic_operation_registry()
    return codec.decode_envelope(RunEnvelope[Piped[DynamicState, PromptContext]], envelope).core


def _plain_core(envelope: str) -> CoreState:
    # lint-waiver: LW-220407 [PLC0415]; the plain strategy is not yet on the search API.
    # > A module-level import makes the dynamic loop's tests fail to import while the plain
    # > strategy is unported; a loader wrapper hides one temporary use. Restore it with the port.
    from vibesys.orchestration.issue_queue.strategy.api import (  # noqa: PLC0415
        IssueQueueStrategyState,
        plain_operation_registry,
    )

    codec = plain_operation_registry()
    return codec.decode_envelope(RunEnvelope[IssueQueueStrategyState], envelope).core


LOOPS = {
    "dynamic": LoopSpec("dynamic", _dynamic_core),
    "plain": LoopSpec("plain", _plain_core),
}


class UnknownLoopError(ValueError):
    """The harness has no spec for this plugin id."""

    def __init__(self, plugin_id: str) -> None:
        super().__init__(f"no loop spec for plugin {plugin_id!r}; known: {sorted(LOOPS)}")


def loop_spec(plugin_id: str) -> LoopSpec:
    """The spec of the plugin, or a typed error naming the known ids."""
    try:
        return LOOPS[plugin_id]
    except KeyError:
        raise UnknownLoopError(plugin_id) from None
