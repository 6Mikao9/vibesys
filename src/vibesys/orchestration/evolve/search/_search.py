"""`EvolveSearch`: the evolutionary search as a pure state machine.

The search keeps its concurrent work as children: one per slot of the open frame. A frame is a
single cold-start attempt, or the children of one generation. `update` takes one event at a
time, gives it to the slot whose decision it answers, admits every slot that finished, and then
looks at the two things that follow from the state rather than from an event: queued slots a
free seat can take, and a frame with nothing left in it, which opens the next one or proposes the
winner.

Children may finish in any order, but an outcome is admitted to the population only once every
lower slot has finished, in slot order, so the population grows exactly as the serial loop's
would. That is what makes a replay reach the same population whatever order the executors
finished in. Everything the search knows about the run besides its own children it reads from the
`RunView`.
"""

from collections.abc import Callable

from vibesys.candidates import api as candidates
from vibesys.hypothesis import RunEnding
from vibesys.orchestration.evolve.search import _context as context
from vibesys.orchestration.evolve.search import _ids as ids
from vibesys.orchestration.evolve.search import _population as population
from vibesys.orchestration.evolve.search import _slot as slot_machine
from vibesys.orchestration.evolve.search._config import EvolveConfig
from vibesys.orchestration.evolve.search._operations import (
    EXPORT_PATCH_KIND,
    INTERPRET_KIND,
    OPERATIONS,
    VERIFY_KIND,
)
from vibesys.orchestration.evolve.search._schemas import STRATEGY_ID
from vibesys.orchestration.evolve.search._slot import Done, Queued, Slot
from vibesys.orchestration.evolve.search._state import (
    ADVISORY_LIMIT,
    STATE_SCHEMA,
    ColdStart,
    EvolveState,
    Finished,
    Generation,
    Selecting,
)
from vibesys.orchestration.evolve.search._values import (
    Advisory,
    Failure,
    FailureKind,
    SlotPlan,
)
from vs_core.api.search import (
    THIS_RUN,
    AdoptionFinished,
    Decision,
    DecisionId,
    Event,
    EventKind,
    Failed,
    FailureReason,
    Finish,
    RunFacts,
    RunResultProposal,
    RunView,
    SearchDeclaration,
    StopRequested,
    Within,
)
from vs_core.api.search import WorkKind as CoreWork

_FINISH_AGAIN = DecisionId(root="finish.1")

type _Step = Callable[[Slot, Event], tuple[Slot, list[Decision]]]

_FOR_THE_RUN = 1.0e18
"""A wait as long as the run, for a seat asked for before any run clock is read.

Core caps every wait at the run deadline, so this is only ever a bound that never binds.
"""


class EvolveSearch:
    """Scientific policy of the evolve search, parameterized by its static config."""

    def __init__(self, config: EvolveConfig) -> None:
        """Bind the search to the policy of one run."""
        self._config = config

    @property
    def declaration(self) -> SearchDeclaration:
        """Operations, capabilities and event kinds the run must (or may) offer."""
        verify = OPERATIONS.schema_ref(VERIFY_KIND)
        interpret = OPERATIONS.schema_ref(INTERPRET_KIND)
        export = OPERATIONS.schema_ref(EXPORT_PATCH_KIND)
        needs_code = self._config.needs_code
        return SearchDeclaration(
            strategy_id=STRATEGY_ID,
            state_schema=STATE_SCHEMA,
            required_operations=(verify, interpret, export) if needs_code else (verify, interpret),
            optional_operations=() if needs_code else (export,),
            optional=frozenset({"suspend"}),
            uses=frozenset(
                {
                    CoreWork.ASK_AGENT,
                    CoreWork.START_ATTEMPT,
                    CoreWork.WITHDRAW,
                    CoreWork.MEASURE,
                    CoreWork.PROPOSE_WINNER,
                    CoreWork.OPERATION,
                }
            ),
            handles=frozenset(
                {
                    EventKind.WORKSPACE_OPENED,
                    EventKind.WORKSPACE_CLOSED,
                    EventKind.TURN_FINISHED,
                    EventKind.MEASUREMENT_FINISHED,
                    EventKind.OPERATION_FINISHED,
                    EventKind.ADOPTION_FINISHED,
                    EventKind.STOP_REQUESTED,
                    EventKind.FAILED,
                }
            ),
        )

    @property
    def state_model(self) -> type[EvolveState]:
        """The class of the persisted state."""
        return EvolveState

    # -- start ----------------------------------------------------------------------------------

    def init(self, config: EvolveConfig, facts: RunFacts) -> tuple[EvolveState, list[Decision]]:
        """Seed the population, and ask for the first cold start's seat."""
        del config  # the search was built with the run's config
        initial = population.search_of(self._config).initial()
        return self._advance(EvolveState(population=initial), facts, None)

    # -- update ---------------------------------------------------------------------------------

    def update(
        self, state: EvolveState, event: Event, view: RunView
    ) -> tuple[EvolveState, list[Decision]]:
        """One event: to the slot it belongs to, then whatever follows from the state."""
        match state.run, event:
            case Finished(), _:
                return self._finished(state, event)
            case Selecting(adopting=adopting), AdoptionFinished():
                return self._finish(state, candidates.update_selection(adopting, event))
            case Selecting(adopting=adopting), Failed(decision=decision) if (
                decision == candidates.PROPOSE
            ):
                return self._finish(state, candidates.update_selection(adopting, event))
            case _, StopRequested():
                return self._stop(state, event, view)
            case _, _:
                return self._route(state, event, view)

    def _route(
        self, state: EvolveState, event: Event, view: RunView
    ) -> tuple[EvolveState, list[Decision]]:
        # an event that answers no decision of this search (an agent's own measurement, a close
        # core asked for) is the run's business, not the search's
        if getattr(event, "decision", None) is None:
            return state, []
        slots, decisions = state.slots.route_event(event, self._step(view))
        return self._after(state.model_copy(update={"slots": slots}), decisions, view)

    def _stop(
        self, state: EvolveState, event: StopRequested, view: RunView
    ) -> tuple[EvolveState, list[Decision]]:
        """An operator stop sets the flag, ends unseated slots and settles seated ones.

        It cancels no turn, and it opens no further frame.
        """
        if event.control.action != "stop":
            return state, []
        state = state.model_copy(update={"stopping": True})
        if not isinstance(state.run, ColdStart | Generation):
            return state, []
        slots, decisions = state.slots.broadcast(event, self._step(view))
        return self._after(state.model_copy(update={"slots": slots}), decisions, view)

    def _step(self, view: RunView) -> _Step:
        """How one slot takes one event, with what it reads of the run."""
        env = slot_machine.Env(config=self._config, view=view)

        def step(slot: Slot, event: Event) -> tuple[Slot, list[Decision]]:
            return slot_machine.update(slot, event, env)

        return step

    def _after(
        self, state: EvolveState, decisions: list[Decision], view: RunView
    ) -> tuple[EvolveState, list[Decision]]:
        state, more = self._advance(state, view.facts, view)
        return state, [*decisions, *more]

    # -- what follows from the state ------------------------------------------------------------------

    def _advance(
        self, state: EvolveState, facts: RunFacts, view: RunView | None
    ) -> tuple[EvolveState, list[Decision]]:
        """Admit finished slots, open and seat what follows, and select when nothing is left."""
        decisions: list[Decision] = []
        while True:
            state = self._admit(state)
            if not isinstance(state.run, ColdStart | Generation):
                return state, decisions
            if state.slots.keys():
                break
            if self._over(state):
                if view is None:  # the first frame is never the last: a cold start is allowed
                    return state, decisions
                state, more = self._select(state, view)
                return state, [*decisions, *more]
            state = self._open(state, facts)
        state, seated = self._seat(state, view)
        return state, [*decisions, *seated]

    def _admit(self, state: EvolveState) -> EvolveState:
        """Admit finished slots in slot order, stopping at the first that is still open."""
        for key, slot in state.slots.in_order():
            if not isinstance(slot.phase, Done):
                break
            state = self._admitted(state, key, slot.plan, slot.phase)
        return state

    def _admitted(self, state: EvolveState, key: str, plan: SlotPlan, done: Done) -> EvolveState:
        found = done.finding
        current, retained = state.population, state.retained
        outcome = population.outcome_of(plan, found, done.settled)
        if outcome is not None and done.settled is not None:
            current, retained = population.admit(
                current, retained, self._config, outcome, done.settled
            )
        update: dict[str, object] = {
            "population": current,
            "retained": retained,
            "slots": state.slots.remove(key),
        }
        if found.advisory is not None:
            noted = Advisory(subject=key, failure=found.advisory)
            update["advisories"] = (*state.advisories, noted)[-ADVISORY_LIMIT:]
        if found.unreachable is not None:
            update["unreachable"] = (*state.unreachable, found.unreachable)
        if plan.cold_start and isinstance(state.run, ColdStart):
            update["run"] = ColdStart(spent=state.run.spent + 1)
        return state.model_copy(update=update)

    def _seeded(self, state: EvolveState) -> bool:
        """Whether an individual has passed, so the search evolves instead of cold-starting."""
        return not population.search_of(self._config).needs_bootstrap(state.population)

    def _over(self, state: EvolveState) -> bool:
        """Whether no slot is open and none will start."""
        if state.stopping:
            return True
        match state.run:
            case ColdStart(spent=spent) if not self._seeded(state):
                return spent >= self._config.bootstrap_max_attempts
            case _:
                return state.population.generation >= self._config.max_generations

    # -- frames -----------------------------------------------------------------------------------

    def _open(self, state: EvolveState, facts: RunFacts) -> EvolveState:
        """Open the next frame: a cold start until one passes, then a generation."""
        match state.run:
            case ColdStart(spent=spent) if not self._seeded(state):
                return self._open_cold_start(state, facts, spent + 1)
            case _:
                return self._open_generation(state, facts)

    def _open_cold_start(self, state: EvolveState, facts: RunFacts, number: int) -> EvolveState:
        """The next cold-start attempt, repairing the last failure's work in progress if any."""
        seed = population.search_of(self._config).wip_seed(state.population)
        seeded = None if seed is None else population.revision_of(state.retained, seed)
        plan = SlotPlan(
            number=number,
            search_generation=0,
            base=seeded or facts.baseline,
            repair_seed=seeded is not None,
        )
        key = ids.bootstrap_key(number)
        return state.model_copy(update={"slots": state.slots.put(key, Slot(key=key, plan=plan))})

    def _open_generation(self, state: EvolveState, facts: RunFacts) -> EvolveState:
        """Begin the next generation: freeze the population and give every slot its proposal."""
        started = population.search_of(self._config).end_generation(state.population)
        slots = state.slots
        for index, proposal in enumerate(population.proposals(self._config, started), start=1):
            key = ids.child_key(started.generation, index)
            parent = (
                None if proposal is None else population.individual(started, proposal.parent_id)
            )
            base = None if parent is None else population.revision_of(state.retained, parent)
            plan = SlotPlan(
                number=index,
                search_generation=started.generation,
                proposal=proposal,
                base=base or facts.baseline,
            )
            if base is None:
                slot = slot_machine.skipped(key, plan, Failure(kind=FailureKind.NO_RETAINED_PARENT))
            else:
                slot = Slot(key=key, plan=plan)
            slots = slots.put(key, slot)
        return state.model_copy(
            update={"population": started, "run": Generation(frozen=started), "slots": slots}
        )

    def _seat(self, state: EvolveState, view: RunView | None) -> tuple[EvolveState, list[Decision]]:
        """Seat queued slots, lowest first, while fewer than ``max_parallelism`` are in flight."""
        config = self._config
        within = Within(limit=_FOR_THE_RUN) if view is None else candidates.until_deadline(view)
        frozen = state.run.frozen if isinstance(state.run, Generation) else state.population
        flying = sum(slot.seated for _, slot in state.slots.in_order())
        slots = state.slots
        decisions: list[Decision] = []
        for key, slot in state.slots.in_order():
            if flying >= config.max_parallelism:
                break
            if not isinstance(slot.phase, Queued):
                continue
            brief = context.mutate_prompt(slot.plan, state.population, frozen, config)
            seated, asked = slot_machine.seat(slot, brief, config, within)
            slots, stamped = slots.spawn(key, seated, asked)
            decisions.extend(stamped)
            flying += 1
        return state.model_copy(update={"slots": slots}), decisions

    # -- selection and the end ----------------------------------------------------------------------

    def _select(self, state: EvolveState, view: RunView) -> tuple[EvolveState, list[Decision]]:
        winner = population.winner_of(state.population, state.retained, self._config)
        if winner is None:
            return self._finish(state, None)
        adopting, decisions = candidates.begin_selection(winner, view)
        return state.model_copy(update={"run": Selecting(adopting=adopting)}), decisions

    def _finish(
        self, state: EvolveState, winner: candidates.Winner | None
    ) -> tuple[EvolveState, list[Decision]]:
        ending = candidates.finish(
            winner, stopping=state.stopping, no_winner_reason=self._no_winner_reason(state)
        )
        return state.model_copy(update={"run": Finished(winner=winner)}), [ending]

    def _no_winner_reason(self, state: EvolveState) -> str:
        if population.search_of(self._config).best(state.population) is not None:
            return "the best individual has no retained settlement to adopt"
        return candidates.ending_reason(
            RunEnding.NO_TRUSTED_RESULT,
            "no cold start passed review and the configured gates in "
            f"{self._config.bootstrap_max_attempts} attempts",
        )

    @staticmethod
    def _finished(state: EvolveState, event: Event) -> tuple[EvolveState, list[Decision]]:
        """The result was proposed. If core refused it, the plain failure is proposed once."""
        match event:
            case Failed(decision=decision) if decision == candidates.FINISH:
                result = RunResultProposal(
                    outcome="failure",
                    reason=candidates.ending_reason(
                        RunEnding.NO_TRUSTED_RESULT, FailureReason.REFUSED.value
                    ),
                )
                plain = Finish(
                    decision_id=_FINISH_AGAIN, scope=THIS_RUN, mode="drain", result=result
                )
                return state.model_copy(update={"run": Finished(winner=None)}), [plain]
            case _:
                return state, []
