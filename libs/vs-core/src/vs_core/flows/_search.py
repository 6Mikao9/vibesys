"""``FlowSearch``: the ``Search`` that runs a root flow, journals it and replays it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from vs_core._registry import ContractError

from ._errors import FlowCodeChanged
from ._runtime import FlowDef, _Run
from ._state import FlowState, Step, digest, key, recorded

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from vs_core._registry import OperationRegistry
    from vs_core.types.common import RunFacts
    from vs_core.types.search import Event, RunView
    from vs_core.types.strategy import Decision, SearchDeclaration

    from ._runtime import Flow
    from ._state import LiveFlows


class FlowSearch[C]:
    """A ``Search`` whose behavior is one root flow function.

    ``update`` keeps the live runtime of the newest state it produced. Given any other state
    (after a restart, or a test branching from an older one) it rebuilds the runtime by replaying
    the journal.

    If the code now produces different decisions than the journal recorded, ``FlowCodeChanged``
    is raised, unless ``flows`` is given: then every running flow is restarted from its latest
    snapshot with the new code (see ``FlowDef``), the journal starts over from there, and the
    decisions of the restart come first in that ``update``'s result. The root flow is registered
    as ``"root"`` and its snapshot is the config. Work done since a flow's snapshot is redone,
    and the results of the decisions it had in flight are dropped when they arrive.
    """

    def __init__(
        self,
        declaration: SearchDeclaration,
        root: Callable[[Flow, C], Coroutine[Any, Any, None]],
        config: C,
        registry: OperationRegistry | None = None,
        flows: tuple[FlowDef, ...] | None = None,
    ) -> None:
        """Build the search; ``registry`` is needed only by flows that wait on operations."""
        self.declaration = declaration
        self._registry = registry
        self._restartable = flows is not None
        self._defs = (FlowDef("root", root, type(config)), *(flows or ()))
        self._config = config
        self._cached: tuple[tuple[int, str], _Run] | None = None

    def fresh(self) -> FlowSearch[C]:
        """A search with the same flows and config and no live runtime, as a restart builds one."""
        root, *flows = self._defs
        return FlowSearch(
            self.declaration,
            root.fn,
            self._config,
            self._registry,
            tuple(flows) if self._restartable else None,
        )

    @property
    def state_model(self) -> type[FlowState]:
        """The class of the persisted state."""
        return FlowState

    def init(self, config: C, facts: RunFacts) -> tuple[FlowState, list[Decision]]:
        """Start the root flow; the journal begins with the step that started it."""
        if config != self._config:
            raise ContractError(("config",), "init must get the config the search was built with")
        run = self._new_run(facts)
        decisions = run.start()
        return self._first_state(run, facts, None, decisions), decisions

    def update(
        self, state: FlowState, event: Event, view: RunView
    ) -> tuple[FlowState, list[Decision]]:
        """Deliver ``event`` to the flows it unblocks and return what they decided."""
        restarted: list[Decision] = []
        try:
            run = self._runtime(state)
        except FlowCodeChanged:
            if not self._restartable:
                raise
            state, run, restarted = self._restart(state, view)
        self._cached = None  # a failed update leaves the runtime half-way: never reuse it
        journaled, kept = recorded(event, self._registry)
        item = journaled or kept
        if item is None:
            raise ContractError(("event",), "an event is journaled as itself or as a record")
        decisions = run.feed(item, view, None)
        step = Step(
            event=journaled,
            recorded=kept,
            reads=run.reads,
            digest=digest(state.journal[-1].digest, item, run.reads, decisions),
        )
        new = state.model_copy(
            update={
                "journal": (*state.journal, step),
                "live": run.checkpoint(),
                "dropped": tuple(run.dropped),
            }
        )
        self._cached = (key(new), run)
        return new, [*restarted, *decisions]

    # -- replay and restart ------------------------------------------------------------------

    def _new_run(self, facts: RunFacts) -> _Run:
        return _Run(self._defs, self._config, facts, self._registry)

    def _first_state(
        self,
        run: _Run,
        facts: RunFacts,
        base: LiveFlows | None,
        decisions: list[Decision],
    ) -> FlowState:
        """The state whose journal has just its first step: the start of the flows."""
        first = Step(reads=run.reads, digest=digest("", None, run.reads, decisions))
        state = FlowState(
            schema_version=1,
            facts=facts,
            journal=(first,),
            live=run.checkpoint(),
            base=base,
        )
        self._cached = (key(state), run)
        return state

    def _runtime(self, state: FlowState) -> _Run:
        """The live runtime for ``state``: the cached one, or one rebuilt by replay."""
        if self._cached is not None and self._cached[0] == key(state):
            return self._cached[1]
        run = self._new_run(state.facts)
        before = ""
        for index, step in enumerate(state.journal):
            item = step.event or step.recorded
            try:
                if item is not None:
                    decisions = run.feed(item, None, step.reads)
                elif state.base is None:
                    decisions = run.start()
                else:
                    decisions = run.restore(state.base, None, step.reads)
            except FlowCodeChanged as changed:
                raise FlowCodeChanged(index) from changed
            if digest(before, item, step.reads, decisions) != step.digest:
                raise FlowCodeChanged(index)
            before = step.digest
        return run

    def _restart(self, state: FlowState, view: RunView) -> tuple[FlowState, _Run, list[Decision]]:
        """Start every running flow again from its latest snapshot, with a fresh journal."""
        if any(live.name is None for live in state.live.flows):
            raise FlowCodeChanged(0)  # a flow without a registered name cannot be restarted
        run = self._new_run(state.facts)
        decisions = run.restore(state.live, view, None)
        restarted = self._first_state(run, state.facts, state.live, decisions)
        return restarted, run, decisions
