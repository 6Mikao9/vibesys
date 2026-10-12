"""`DynamicSearch`: the dynamic search as a pure state machine.

The search keeps its concurrent work as children: the input measurement, one planning call at a
time and one child per workstream. `update` takes one event at a time, gives it to the child
whose decision it answers, folds any child that finished, and then looks at the two things that
follow from the state rather than from an event: free slots to plan for, and a search with
nothing left to run, which proposes its winner.

Everything it knows about the run besides its own children it reads from the `RunView`.
"""

from collections.abc import Callable
from typing import cast

from vibesys.candidates import api as candidates
from vibesys.hypothesis import RunEnding
from vibesys.orchestration.dynamic.search import _context as context
from vibesys.orchestration.dynamic.search import _portfolio as portfolio
from vibesys.orchestration.dynamic.search import _workstream as workstream
from vibesys.orchestration.dynamic.search._config import DynamicConfig
from vibesys.orchestration.dynamic.search._ids import (
    BASELINE_KEY,
    PLANNING_PREFIX,
    planning_key,
    workstream_key,
)
from vibesys.orchestration.dynamic.search._operations import (
    INTERPRET_KIND,
    OPERATIONS,
    VERIFY_KIND,
    InterpretEvidence,
)
from vibesys.orchestration.dynamic.search._parents import ParentConflictError, ingest, resolve
from vibesys.orchestration.dynamic.search._plan import PlanCheck
from vibesys.orchestration.dynamic.search._recipe import measurement_recipe
from vibesys.orchestration.dynamic.search._schemas import STRATEGY_ID
from vibesys.orchestration.dynamic.search._state import (
    STATE_SCHEMA,
    DynamicState,
    Finished,
    Part,
    PlannerGate,
    Searching,
    Selecting,
)
from vibesys.orchestration.dynamic.search._values import (
    FinishedWork,
    HypothesisRecord,
    RoundRecord,
    WorkKind,
    WorkPlan,
)
from vs_core.api import (
    THIS_RUN,
    AdoptionFinished,
    Children,
    Decision,
    DecisionId,
    Event,
    EventKind,
    Failed,
    FailureReason,
    Finish,
    RevisionRef,
    RunFacts,
    RunResultProposal,
    RunView,
    SearchDeclaration,
    StopRequested,
    TimeReached,
    UnexpectedEvent,
)
from vs_core.api import WorkKind as CoreWork

_FINISH_AGAIN = DecisionId(root="finish.1")

type _Step = Callable[[Part, Event], tuple[Part, list[Decision]]]


class DynamicSearch:
    """Scientific policy of the dynamic search, parameterized by its static config."""

    def __init__(self, config: DynamicConfig) -> None:
        """Bind the search to the policy of one run."""
        self._config = config

    @property
    def declaration(self) -> SearchDeclaration:
        """Operations, capabilities and event kinds the run must (or may) offer."""
        return SearchDeclaration(
            strategy_id=STRATEGY_ID,
            state_schema=STATE_SCHEMA,
            required_operations=(
                OPERATIONS.schema_ref(VERIFY_KIND),
                OPERATIONS.schema_ref(INTERPRET_KIND),
            ),
            optional=frozenset({"suspend", "profile-capture"}),
            uses=frozenset(
                {
                    CoreWork.ASK_AGENT,
                    CoreWork.START_ATTEMPT,
                    CoreWork.WITHDRAW,
                    CoreWork.MEASURE,
                    CoreWork.PROPOSE_WINNER,
                    CoreWork.OPERATION,
                    CoreWork.WAKE_AT,
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
                    EventKind.TIME_REACHED,
                    EventKind.FAILED,
                }
            ),
        )

    @property
    def state_model(self) -> type[DynamicState]:
        """The class of the persisted state."""
        return DynamicState

    # -- start ----------------------------------------------------------------------------------

    def init(self, config: DynamicConfig, facts: RunFacts) -> tuple[DynamicState, list[Decision]]:
        """Measure the input, and plan at once when there is nothing to measure."""
        baseline, decisions = candidates.begin(config, facts, self._measuring(facts))
        parts, stamped = Children[str, Part]().spawn(BASELINE_KEY, baseline, decisions)
        state = DynamicState(parts=parts)
        return self._advance(state, stamped, facts, None)

    # -- update ---------------------------------------------------------------------------------

    def update(
        self, state: DynamicState, event: Event, view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        """One event: to the child it belongs to, then whatever follows from the state."""
        match state.run, event:
            case Finished() as finished, _:
                return self._finished(state, finished, event)
            case Selecting(adopting=adopting), AdoptionFinished():
                return self._selected(state, candidates.update_selection(adopting, event), view)
            case Selecting(adopting=adopting), Failed(decision=decision) if (
                decision == candidates.PROPOSE
            ):
                return self._selected(state, candidates.update_selection(adopting, event), view)
            case _, StopRequested():
                return self._stop(state, event, view)
            case _, TimeReached():
                return self._wake(state, event, view)
            case _, _:
                return self._route(state, event, view)

    def _route(
        self, state: DynamicState, event: Event, view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        # an event that answers no decision of this search (an agent's own measurement, a close
        # core asked for) is the run's business, not the search's
        if getattr(event, "decision", None) is None:
            return state, []
        parts, decisions = state.parts.route_event(event, self._step(state, view))
        return self._after(state.model_copy(update={"parts": parts}), decisions, view)

    def _wake(
        self, state: DynamicState, event: TimeReached, view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        key = next(
            (key for key, part in state.parts.in_order() if isinstance(part, portfolio.BackingOff)),
            None,
        )
        if key is None:
            return state, []  # the wake-up of a backoff that is over
        parts, decisions = state.parts.route(key, event, self._step(state, view))
        return self._after(state.model_copy(update={"parts": parts}), decisions, view)

    def _stop(
        self, state: DynamicState, event: StopRequested, view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        """An operator stop sets the flag and settles started work; it cancels no turn."""
        if event.control.action != "stop":
            return state, []
        state = state.model_copy(update={"stopping": True})
        if not isinstance(state.run, Searching):
            return state, []
        step = self._step(state, view)

        def stop(part: Part, found: Event) -> tuple[Part, list[Decision]]:
            return step(part, found) if isinstance(part, workstream.Workstream) else (part, [])

        parts, decisions = state.parts.broadcast(event, stop)
        return self._after(state.model_copy(update={"parts": parts}), decisions, view)

    def _step(self, state: DynamicState, view: RunView) -> _Step:
        """How one child takes one event, with what it reads of the search and the run."""
        config = self._config
        baseline = _baseline(state)
        env = workstream.Env(
            config=config,
            baseline=baseline,
            view=view,
            recipe=measurement_recipe(config, view.facts),
        )
        measuring = self._measuring(view.facts)

        def step(part: Part, event: Event) -> tuple[Part, list[Decision]]:
            match part:
                case workstream.Workstream():
                    return workstream.update(part, event, env)
                case portfolio.Asking() | portfolio.BackingOff():
                    world = self._world(state, view.facts.baseline, view)
                    return portfolio.update(part, event, world, view)
                case portfolio.Scheduled() | portfolio.GaveUp():
                    raise UnexpectedEvent(part, event)
                case _:
                    return candidates.update(part, event, view, config, measuring)

        return step

    def _after(
        self, state: DynamicState, decisions: list[Decision], view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        state, folded = self._fold(state, view)
        return self._advance(state, [*decisions, *folded], view.facts, view)

    # -- folding finished children ----------------------------------------------------------------

    def _fold(self, state: DynamicState, view: RunView) -> tuple[DynamicState, list[Decision]]:
        """Record every child that finished, in key order, until none is left."""
        decisions: list[Decision] = []
        while True:
            found = next(
                ((key, part) for key, part in state.parts.in_order() if _finished(part)), None
            )
            if found is None:
                return state, decisions
            key, part = found
            state = state.model_copy(update={"parts": state.parts.remove(key)})
            match part:
                case workstream.Workstream():
                    state = self._record(state, part, view)
                case portfolio.Scheduled(check=check):
                    state, more = self._schedule(state, check, view)
                    decisions.extend(more)
                case portfolio.GaveUp(problem=problem, stuck=stuck, blocked_at_done=at_done):
                    gate = PlannerGate(failed=stuck, blocked_at_done=at_done, last_error=problem)
                    state = state.model_copy(update={"gate": gate})
                case _:
                    raise UnexpectedEvent(part, None)

    def _schedule(
        self, state: DynamicState, check: PlanCheck, view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        """Apply the plan's park and abandon decisions and start one workstream per entry."""
        hypotheses = list(state.hypotheses)
        for change in check.updates:
            index = next(
                i for i, item in enumerate(hypotheses) if item.hypothesis_id == change.hypothesis_id
            )
            hypotheses[index] = hypotheses[index].model_copy(
                update={
                    "strategy": change.disposition,
                    "reason": change.reason,
                    "reason_kind": change.reason_kind,
                }
            )
        state = state.model_copy(
            update={
                "hypotheses": tuple(hypotheses),
                "gate": state.gate.model_copy(update={"last_error": None, "blocked_at_done": None}),
            }
        )
        env = workstream.Env(
            config=self._config,
            baseline=_baseline(state),
            view=view,
            recipe=measurement_recipe(self._config, view.facts),
        )
        decisions: list[Decision] = []
        for plan in check.accepted:
            sequence = _started(state) + 1
            parent, lineage = _parent_of(state, view, plan)
            prior = _prior_round(state, plan)
            state = self._with_hypothesis(state, plan, sequence)
            child, started = workstream.begin(
                plan, sequence, parent, lineage, prior, stopping=state.stopping, env=env
            )
            parts, stamped = state.parts.spawn(
                workstream_key(sequence, plan.work_id), child, started
            )
            state = state.model_copy(update={"parts": parts})
            decisions.extend(stamped)
        return state, decisions

    @staticmethod
    def _with_hypothesis(state: DynamicState, plan: WorkPlan, sequence: int) -> DynamicState:
        if plan.kind is not WorkKind.IMPLEMENT:
            return state
        hypotheses = list(state.hypotheses)
        index = next((i for i, h in enumerate(hypotheses) if h.hypothesis_id == plan.work_id), None)
        if index is None:
            hypotheses.append(
                HypothesisRecord(
                    hypothesis_id=plan.work_id,
                    title=plan.title,
                    hypothesis=plan.hypothesis,
                    first_sequence=sequence,
                    lineage_parent_id=plan.parent_hypothesis_id,
                    last_task=plan.task,
                )
            )
        else:
            hypotheses[index] = hypotheses[index].model_copy(update={"last_task": plan.task})
        return state.model_copy(update={"hypotheses": tuple(hypotheses)})

    def _record(
        self, state: DynamicState, ws: workstream.Workstream, view: RunView
    ) -> DynamicState:
        """Keep what a finished workstream leaves behind: its record, round and parent."""
        done = ws.phase
        if not isinstance(done, workstream.Done):
            raise UnexpectedEvent(done, None)
        profile = ws.plan.kind is WorkKind.PROFILE
        kept = FinishedWork(
            sequence=ws.sequence,
            plan=ws.plan,
            parent=ws.parent,
            parent_hypothesis_id=ws.parent_hypothesis_id,
            failure=done.failure,
            readings=done.readings if profile else (),
            proof=done.proof if profile else None,
        )
        update: dict[str, object] = {"finished": (*state.finished, kept)}
        if done.refunded:
            update["refunded"] = state.refunded + 1
        if done.withheld is not None:
            root = done.withheld.revision_id.root
            if root not in state.withheld:
                update["withheld"] = (*state.withheld, root)
            update["gate"] = state.gate.model_copy(update={"blocked_at_done": None})
        if done.unreachable is not None:
            update["unreachable"] = (*state.unreachable, done.unreachable)
        state = state.model_copy(update=update)
        if profile or done.settlement is None:
            return state
        state = _with_round(state, ws, workstream.round_of(ws, done, self._config))
        snapshot = workstream.snapshot_of(ws, done)
        if snapshot is None:
            return state
        try:
            parents = ingest(state.parents, snapshot, view)
        except ParentConflictError:
            return state
        return state.model_copy(update={"parents": parents})

    # -- what follows from the state ------------------------------------------------------------------

    def _advance(
        self,
        state: DynamicState,
        decisions: list[Decision],
        facts: RunFacts,
        view: RunView | None,
    ) -> tuple[DynamicState, list[Decision]]:
        """Plan when slots are free, and select when nothing is left to run."""
        if self._may_plan(state):
            world = self._world(state, facts.baseline, view)
            planning, asked = portfolio.begin(world, state.gate.last_error)
            calls = state.calls + 1
            parts, stamped = state.parts.spawn(planning_key(calls), planning, asked)
            gate = state.gate.model_copy(update={"blocked_at_done": None})
            state = state.model_copy(update={"parts": parts, "calls": calls, "gate": gate})
            decisions = [*decisions, *stamped]
        if view is not None and self._search_over(state):
            state, more = self._select(state, facts, view)
            decisions = [*decisions, *more]
        return state, decisions

    def _may_plan(self, state: DynamicState) -> bool:
        gate = state.gate
        if not isinstance(state.run, Searching) or state.stopping or gate.failed:
            return False
        if _planning(state) or not candidates.resolved(_baseline(state)):
            return False
        if self._capacity(state) == 0:
            return False
        return gate.blocked_at_done is None or len(state.finished) > gate.blocked_at_done

    def _capacity(self, state: DynamicState) -> int:
        left = context.remaining(self._config, started=_started(state), refunded=state.refunded)
        return context.free_slots(self._config, active=len(_running(state)), left=left)

    def _search_over(self, state: DynamicState) -> bool:
        """Whether nothing is in flight and nothing more will be scheduled."""
        if not isinstance(state.run, Searching) or _running(state) or _planning(state):
            return False
        left = context.remaining(self._config, started=_started(state), refunded=state.refunded)
        return (
            state.stopping
            or state.gate.failed
            or (candidates.resolved(_baseline(state)) and left == 0)
        )

    def _world(
        self, state: DynamicState, base: RevisionRef, view: RunView | None
    ) -> context.PlanWorld:
        offered = (
            () if view is None else context.offered_parents(state.parents, state.withheld, view)
        )
        return context.PlanWorld(
            config=self._config,
            base=base,
            baseline=_baseline(state),
            hypotheses=state.hypotheses,
            running=_running(state),
            finished=state.finished,
            refunded=state.refunded,
            offered=offered,
        )

    def _measuring(self, facts: RunFacts) -> candidates.InputMeasurement:
        return candidates.InputMeasurement(
            recipe=measurement_recipe(self._config, facts),
            interpret=lambda refs: InterpretEvidence(evidence=refs),
            operation_seconds=self._config.operation_seconds,
        )

    # -- selection and the end ----------------------------------------------------------------------

    def _select(
        self, state: DynamicState, facts: RunFacts, view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        rounds = _eligible(state)
        trusted = candidates.trusted(_baseline(state))
        if (state.gate.failed and not rounds) or (not rounds and not trusted):
            return self._finish(state, None)
        winner = candidates.choose_winner(rounds, self._config, facts.baseline)
        adopting, decisions = candidates.begin_selection(winner, view)
        return state.model_copy(update={"run": Selecting(adopting=adopting)}), decisions

    def _selected(
        self, state: DynamicState, winner: candidates.Winner | None, view: RunView
    ) -> tuple[DynamicState, list[Decision]]:
        del view
        return self._finish(state, winner)

    def _finish(
        self, state: DynamicState, winner: candidates.Winner | None
    ) -> tuple[DynamicState, list[Decision]]:
        ending = candidates.finish(
            winner, stopping=state.stopping, no_winner_reason=self._no_winner_reason(state)
        )
        return state.model_copy(update={"run": Finished(winner=winner)}), [ending]

    def _no_winner_reason(self, state: DynamicState) -> str:
        baseline = _baseline(state)
        problem = state.gate.last_error
        asked = problem.kind.value if problem is not None else ""
        if candidates.trusted(baseline) or _eligible(state):
            return asked or "no candidate could be adopted"
        why = candidates.failure(baseline)
        detail = why.kind.value if why is not None else "the input did not pass its measurement"
        if asked:
            detail = f"{detail}; the planner's last reply: {asked}"
        return candidates.ending_reason(RunEnding.NO_TRUSTED_RESULT, detail)

    @staticmethod
    def _finished(
        state: DynamicState, finished: Finished, event: Event
    ) -> tuple[DynamicState, list[Decision]]:
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
                del finished
                return state, []


def _finished(part: Part) -> bool:
    if isinstance(part, workstream.Workstream):
        return isinstance(part.phase, workstream.Done)
    return isinstance(part, portfolio.Scheduled | portfolio.GaveUp)


def _baseline(state: DynamicState) -> candidates.Baseline:
    return cast("candidates.Baseline", state.parts.get(BASELINE_KEY))


def _running(state: DynamicState) -> tuple[workstream.Workstream, ...]:
    return tuple(
        part for _, part in state.parts.in_order() if isinstance(part, workstream.Workstream)
    )


def _planning(state: DynamicState) -> bool:
    keys = state.parts.keys()
    return any(key.startswith(PLANNING_PREFIX) for key in keys)


def _started(state: DynamicState) -> int:
    """Workstreams ever scheduled: the finished ones and the ones still running."""
    return len(state.finished) + len(_running(state))


def _eligible(state: DynamicState) -> tuple[candidates.EligibleRound, ...]:
    return tuple(
        candidates.EligibleRound(
            hypothesis_id=hypothesis.hypothesis_id,
            sequence=row.sequence,
            settlement=row.settlement,
            candidate=row.candidate,
            metrics=row.metrics,
        )
        for hypothesis in state.hypotheses
        for row in hypothesis.rounds
        if row.eligible and row.candidate is not None and row.settlement is not None
    )


def _prior_round(state: DynamicState, plan: WorkPlan) -> RoundRecord | None:
    record = next((item for item in state.hypotheses if item.hypothesis_id == plan.work_id), None)
    return record.rounds[-1] if record is not None and record.rounds else None


def _parent_of(
    state: DynamicState, view: RunView, plan: WorkPlan
) -> tuple[RevisionRef, str | None]:
    """Freeze the parent revision this workstream builds on, resolved only from offers."""
    prior = next((item for item in state.hypotheses if item.hypothesis_id == plan.work_id), None)
    if plan.kind is WorkKind.IMPLEMENT and plan.continue_hypothesis and prior is not None:
        last = prior.rounds[-1] if prior.rounds else None
        if last is not None and last.candidate is not None:
            return last.candidate, prior.lineage_parent_id
        earlier = [item for item in state.finished if item.plan.work_id == plan.work_id]
        if earlier:
            return earlier[-1].parent, earlier[-1].parent_hypothesis_id
    if plan.parent_hypothesis_id is not None:
        snapshot = resolve(state.parents, view, plan.parent_hypothesis_id, plan.parent_revision)
        if snapshot is not None:
            return snapshot.revision, plan.parent_hypothesis_id
    return view.facts.baseline, None


def _with_round(state: DynamicState, ws: workstream.Workstream, row: RoundRecord) -> DynamicState:
    hypotheses = list(state.hypotheses)
    index = next(
        (i for i, item in enumerate(hypotheses) if item.hypothesis_id == ws.plan.work_id), None
    )
    if index is None:
        return state
    current = hypotheses[index]
    hypotheses[index] = current.model_copy(
        update={
            "rounds": (*current.rounds, row),
            "continuation_rounds": current.continuation_rounds
            + (1 if ws.plan.continue_hypothesis else 0),
        }
    )
    return state.model_copy(update={"hypotheses": tuple(hypotheses)})
