"""`MultiSearch`: the multi-agent search as a pure state machine.

The search keeps its work as children: the input measurement, then one design or one round at a
time. `update` gives each event to the child whose decision it answers, folds any child that
finished, and then looks at the two things that follow from the state rather than from an event:
the next hypothesis or round to open, and a search with nothing left to run, which proposes its
winner.

A round that is done is closed in the hypothesis ledger from its own state, whether or not core
settled it: a round core never admitted, or whose settlement core refused, is counted as failed so
the search moves on; a round the operator stopped is cleared without counting.

Everything it knows about the run besides its own children it reads from the `RunView`.
"""

from collections.abc import Callable
from typing import cast

from vibesys.candidates import api as candidates
from vibesys.hypothesis import (
    AgentAttribution,
    Continue,
    Finished,
    JudgeOutcome,
    JudgeReviewed,
    JudgeSkipped,
    JudgeSkipReason,
    RunEnding,
    reviewed,
    rounds,
)
from vibesys.metrics import FrameworkBenchmarkOutcome, MetricRow, reading_of
from vibesys.orchestration.multi.search import _design as design
from vibesys.orchestration.multi.search import _round as round_
from vibesys.orchestration.multi.search._config import MultiConfig
from vibesys.orchestration.multi.search._ids import BASELINE_KEY, DESIGN_KEY, round_key
from vibesys.orchestration.multi.search._operations import (
    CAPTURE_KIND,
    INTERPRET_KIND,
    OPERATIONS,
    VALIDATE_LOCAL_KIND,
    VERIFY_KIND,
    InterpretEvidence,
)
from vibesys.orchestration.multi.search._recipe import measurement_recipe
from vibesys.orchestration.multi.search._schemas import STRATEGY_ID
from vibesys.orchestration.multi.search._state import (
    STATE_SCHEMA,
    MultiState,
    Part,
    Searching,
    Selecting,
)
from vibesys.orchestration.multi.search._state import (
    Finished as RunFinished,
)
from vibesys.orchestration.multi.search._values import NOTE_LIMIT, Note, Repair, RepairKind
from vibesys.serial_search.api import (
    RoundOutcome,
    decode_ledger,
    describe,
    describe_unmeasured,
    encode_ledger,
    focus_policy,
    giveup_reason,
    record_round,
)
from vs_core.api.search import (
    THIS_RUN,
    AdoptionFinished,
    Children,
    Decision,
    DecisionId,
    Event,
    EventKind,
    EvidenceKind,
    Failed,
    FailureReason,
    Finish,
    RevisionRef,
    RunFacts,
    RunResultProposal,
    RunView,
    SearchDeclaration,
    StopRequested,
    UnexpectedEvent,
)
from vs_core.api.search import WorkKind as CoreWork

_FINISH_AGAIN = DecisionId(root="finish.1")

type _Step = Callable[[Part, Event], tuple[Part, list[Decision]]]


class MultiSearch:
    """Scientific policy of the multi-agent search, parameterized by its static config."""

    def __init__(self, config: MultiConfig) -> None:
        """Bind the search to the policy of one run."""
        self._config = config

    @property
    def declaration(self) -> SearchDeclaration:
        """Operations, capabilities and event kinds the run must (or may) offer."""
        core = (OPERATIONS.schema_ref(VERIFY_KIND), OPERATIONS.schema_ref(INTERPRET_KIND))
        capture = (OPERATIONS.schema_ref(CAPTURE_KIND),)
        validation = OPERATIONS.schema_ref(VALIDATE_LOCAL_KIND)
        guided = self._config.attribution is not None
        return SearchDeclaration(
            strategy_id=STRATEGY_ID,
            state_schema=STATE_SCHEMA,
            required_operations=(*core, *capture) if guided else core,
            optional_operations=(validation,) if guided else (*capture, validation),
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
    def state_model(self) -> type[MultiState]:
        """The class of the persisted state."""
        return MultiState

    # -- start ----------------------------------------------------------------------------------

    def init(self, config: MultiConfig, facts: RunFacts) -> tuple[MultiState, list[Decision]]:
        """Measure the input, and design at once when there is nothing to measure."""
        baseline, decisions = candidates.begin(config, facts, self._measuring(facts))
        parts, stamped = Children[str, Part]().spawn(BASELINE_KEY, baseline, decisions)
        return self._advance(MultiState(parts=parts), stamped, facts, None)

    # -- update ---------------------------------------------------------------------------------

    def update(
        self, state: MultiState, event: Event, view: RunView
    ) -> tuple[MultiState, list[Decision]]:
        """One event: to the child it belongs to, then whatever follows from the state."""
        match state.run, event:
            case RunFinished() as finished, _:
                return self._finished(state, finished, event)
            case Selecting(adopting=adopting), AdoptionFinished():
                return self._selected(state, candidates.update_selection(adopting, event))
            case Selecting(adopting=adopting), Failed(decision=decision) if (
                decision == candidates.PROPOSE
            ):
                return self._selected(state, candidates.update_selection(adopting, event))
            case _, StopRequested():
                return self._stop(state, event, view)
            case _, _:
                return self._route(state, event, view)

    def _route(
        self, state: MultiState, event: Event, view: RunView
    ) -> tuple[MultiState, list[Decision]]:
        # an event that answers no decision of this search (an agent's own measurement, a close
        # core asked for) is the run's business, not the search's
        if getattr(event, "decision", None) is None:
            return state, []
        parts, decisions = state.parts.route_event(event, self._step(state, view))
        return self._after(state.model_copy(update={"parts": parts}), decisions, view)

    def _stop(
        self, state: MultiState, event: StopRequested, view: RunView
    ) -> tuple[MultiState, list[Decision]]:
        """An operator stop sets the flag and settles started work; it cancels no turn."""
        if event.control.action != "stop":
            return state, []
        state = state.model_copy(update={"stopping": True})
        if not isinstance(state.run, Searching):
            return state, []
        step = self._step(state, view)

        def stop(part: Part, found: Event) -> tuple[Part, list[Decision]]:
            return step(part, found) if isinstance(part, round_.Round) else (part, [])

        parts, decisions = state.parts.broadcast(event, stop)
        return self._after(state.model_copy(update={"parts": parts}), decisions, view)

    def _step(self, state: MultiState, view: RunView) -> _Step:
        """How one child takes one event, with what it reads of the search and the run."""
        config = self._config
        measuring = self._measuring(view.facts)
        round_env = round_.Env(
            config=config,
            baseline=_baseline(state),
            view=view,
            recipe=measurement_recipe(config, view.facts),
        )
        design_env = design.Env(config=config, baseline=view.facts.baseline)

        def step(part: Part, event: Event) -> tuple[Part, list[Decision]]:
            match part:
                case round_.Round():
                    return round_.update(part, event, round_env)
                case design.Design():
                    return design.update(part, event, design_env)
                case _:
                    return candidates.update(part, event, view, config, measuring)

        return step

    def _after(
        self, state: MultiState, decisions: list[Decision], view: RunView
    ) -> tuple[MultiState, list[Decision]]:
        state, folded = self._fold(state, view)
        return self._advance(state, [*decisions, *folded], view.facts, view)

    # -- folding finished children ----------------------------------------------------------------

    def _fold(self, state: MultiState, view: RunView) -> tuple[MultiState, list[Decision]]:
        """Take back every child that finished, in key order, until none is left."""
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
                case round_.Round():
                    state = self._record(state, part)
                case design.Design():
                    state, more = self._designed(state, part, view)
                    decisions.extend(more)
                case _:
                    raise UnexpectedEvent(part, None)

    def _designed(
        self, state: MultiState, finished: design.Design, view: RunView
    ) -> tuple[MultiState, list[Decision]]:
        """Open the round a design ended with, or note that designing is over."""
        phase = finished.phase
        state = _noted(state, finished.notes)
        match phase:
            case design.Designed(start=start):
                if state.stopping:
                    # the run is ending: the plan is dropped, keeping only what the design learned
                    return state.model_copy(update={"ledger": finished.ledger}), []
                return self._open(state, start, view)
            case design.Abandoned(giveup=giveup):
                return state.model_copy(update={"ledger": finished.ledger, "giveup": giveup}), []
            case _:
                raise UnexpectedEvent(phase, None)

    def _open(
        self, state: MultiState, start: round_.RoundStart, view: RunView
    ) -> tuple[MultiState, list[Decision]]:
        env = round_.Env(
            config=self._config,
            baseline=_baseline(state),
            view=view,
            recipe=measurement_recipe(self._config, view.facts),
        )
        opened, decisions = round_.begin(start, env)
        parts, stamped = state.parts.spawn(round_key(start.number), opened, decisions)
        return state.model_copy(update={"parts": parts}), stamped

    def _record(self, state: MultiState, rnd: round_.Round) -> MultiState:
        """Keep what a done round leaves behind, closing it in the ledger unless it was stopped."""
        done = rnd.phase
        if not isinstance(done, round_.Done):
            raise UnexpectedEvent(done, None)
        state = _noted(state, rnd.notes)
        update: dict[str, object] = {
            "ledger": rnd.ledger,
            "unreachable": (*state.unreachable, *rnd.unreachable),
        }
        if rnd.stopped:
            return state.model_copy(update=update)
        update.update(self._closed(state, rnd, done))
        return state.model_copy(update=update)

    def _closed(self, state: MultiState, rnd: round_.Round, done: round_.Done) -> dict[str, object]:
        """The state a round that is done changes: its ledger row, its outcome and its checkpoint."""
        config = self._config
        reply = rnd.response
        benchmark, metric = _benchmark_outcome(rnd)
        settlement = done.settlement
        retained = None if settlement is None else settlement.candidate
        passed = rnd.passed and rnd.failure is None
        ledger = decode_ledger(rnd.ledger, config.metric_space)
        closed = reviewed.close_reviewed_round(
            config.hypotheses,
            ledger,
            reviewed.ReviewedClosing(
                round_number=rnd.number,
                plan=rnd.plan,
                reply=reply,
                judge=_judge_outcome(rnd, replied=reply is not None),
                passed=passed,
                feedback=None if passed else _feedback(rnd),
                official_reason=None if rnd.official is None or not passed else rnd.official.value,
                benchmark=benchmark,
                benchmark_metric=metric,
                candidate_commit=None if retained is None else retained.revision_id.root,
                attempts=rnd.spent,
                accuracy_configured=config.accuracy_configured,
                benchmark_configured=config.benchmark_configured,
                implementer=AgentAttribution("core", None, None),
                revalidation_required=rnd.revalidation_required,
            ),
        )
        closed = record_round(closed, focus_policy(config.attribution), passed=passed)
        outcomes = state.outcomes
        if settlement is not None:
            reading = reading_of(rnd.readings, EvidenceKind.BENCHMARK)
            outcomes = (
                *outcomes,
                RoundOutcome(
                    round_number=rnd.number,
                    hypothesis_id=rnd.hypothesis_id,
                    settlement=settlement.settlement_id,
                    candidate=retained,
                    eligible=settlement.eligible,
                    metrics=() if reading is None else reading.metrics,
                ),
            )
        return {
            "ledger": encode_ledger(closed),
            "closed": state.closed + 1,
            "checkpoint": retained if retained is not None else state.checkpoint,
            "outcomes": outcomes,
        }

    # -- what follows from the state ------------------------------------------------------------------

    def _advance(
        self,
        state: MultiState,
        decisions: list[Decision],
        facts: RunFacts,
        view: RunView | None,
    ) -> tuple[MultiState, list[Decision]]:
        """Open the next hypothesis or round, and select when nothing is left to run."""
        if self._may_open(state):
            state, opened = self._open_next(state, facts, view)
            decisions = [*decisions, *opened]
        if view is not None and self._search_over(state):
            state, more = self._select(state, facts, view)
            decisions = [*decisions, *more]
        return state, decisions

    def _open_next(
        self, state: MultiState, facts: RunFacts, view: RunView | None
    ) -> tuple[MultiState, list[Decision]]:
        config = self._config
        ledger = decode_ledger(state.ledger, config.metric_space)
        number = state.closed + 1
        match rounds.next_round(config.hypotheses, ledger, number):
            case Finished():
                return state, []
            case Continue(hypothesis=hypothesis) if view is not None:
                plan = hypothesis.plan
                carried = (
                    Repair(kind=RepairKind.CARRIED, text=hypothesis.feedback)
                    if hypothesis.feedback
                    else None
                )
                start = round_.RoundStart(
                    number=number,
                    hypothesis_id=plan.hypothesis_id,
                    plan_json=plan.model_dump_json(),
                    base=_build_base(state, facts),
                    ledger=state.ledger,
                    carried=carried,
                    revalidation_required=hypothesis.gate_revalidation_pending,
                    planned=design.planned_reason(config, ledger, plan, number),
                )
                return self._open(state, start, view)
            case Continue():
                return state, []  # a hypothesis cannot be active before the first round closed
            case _:
                env = design.Env(config=config, baseline=facts.baseline)
                started, asked = design.begin(number, _build_base(state, facts), state.ledger, env)
                parts, stamped = state.parts.spawn(DESIGN_KEY, started, asked)
                return state.model_copy(update={"parts": parts}), stamped

    def _may_open(self, state: MultiState) -> bool:
        return (
            isinstance(state.run, Searching)
            and not state.stopping
            and state.giveup is None
            and not _in_flight(state)
            and candidates.resolved(_baseline(state))
            and state.closed < self._config.max_rounds
        )

    def _search_over(self, state: MultiState) -> bool:
        """Whether nothing is in flight and nothing more will be opened."""
        if not isinstance(state.run, Searching) or _in_flight(state):
            return False
        if state.stopping or state.giveup is not None:
            return True
        if not candidates.resolved(_baseline(state)):
            return False
        if state.closed >= self._config.max_rounds:
            return True
        config = self._config
        ledger = decode_ledger(state.ledger, config.metric_space)
        return isinstance(rounds.next_round(config.hypotheses, ledger, state.closed + 1), Finished)

    def _measuring(self, facts: RunFacts) -> candidates.InputMeasurement:
        return candidates.InputMeasurement(
            recipe=measurement_recipe(self._config, facts),
            interpret=lambda refs: InterpretEvidence(evidence=refs),
            operation_seconds=self._config.operation_seconds,
        )

    # -- selection and the end ----------------------------------------------------------------------

    def _select(
        self, state: MultiState, facts: RunFacts, view: RunView
    ) -> tuple[MultiState, list[Decision]]:
        eligible = _eligible(state)
        trusted = candidates.trusted(_baseline(state))
        if (state.giveup is not None and not eligible) or (not eligible and not trusted):
            return self._finish(state, None)
        winner = candidates.choose_winner(eligible, self._config, facts.baseline)
        adopting, decisions = candidates.begin_selection(winner, view)
        return state.model_copy(update={"run": Selecting(adopting=adopting)}), decisions

    def _selected(
        self, state: MultiState, winner: candidates.Winner | None
    ) -> tuple[MultiState, list[Decision]]:
        return self._finish(state, winner)

    def _finish(
        self, state: MultiState, winner: candidates.Winner | None
    ) -> tuple[MultiState, list[Decision]]:
        ending = candidates.finish(
            winner, stopping=state.stopping, no_winner_reason=self._no_winner_reason(state)
        )
        return state.model_copy(update={"run": RunFinished(winner=winner)}), [ending]

    def _no_winner_reason(self, state: MultiState) -> str:
        baseline = _baseline(state)
        asked = (
            "" if state.giveup is None else giveup_reason(state.giveup, self._config.attribution)
        )
        if candidates.trusted(baseline) or _eligible(state):
            return asked or "no candidate could be adopted"
        why = candidates.failure(baseline)
        detail = why.kind.value if why is not None else "the input did not pass its measurement"
        if asked:
            detail = f"{detail}; {asked}"
        return candidates.ending_reason(RunEnding.NO_TRUSTED_RESULT, detail)

    @staticmethod
    def _finished(
        state: MultiState, finished: RunFinished, event: Event
    ) -> tuple[MultiState, list[Decision]]:
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
                return state.model_copy(update={"run": RunFinished(winner=None)}), [plain]
            case _:
                del finished
                return state, []


def _finished(part: Part) -> bool:
    match part:
        case round_.Round():
            return isinstance(part.phase, round_.Done)
        case design.Design():
            return isinstance(part.phase, design.Designed | design.Abandoned)
        case _:
            return False


def _baseline(state: MultiState) -> candidates.Baseline:
    return cast("candidates.Baseline", state.parts.get(BASELINE_KEY))


def _in_flight(state: MultiState) -> bool:
    """Whether a design or a round is open."""
    return any(isinstance(part, design.Design | round_.Round) for _, part in state.parts.in_order())


def _build_base(state: MultiState, facts: RunFacts) -> RevisionRef:
    """The newest revision a later round may build on: a retained checkpoint, else the input."""
    return state.checkpoint or facts.baseline


def _eligible(state: MultiState) -> tuple[candidates.EligibleRound, ...]:
    return tuple(
        candidates.EligibleRound(
            hypothesis_id=item.hypothesis_id,
            sequence=item.round_number,
            settlement=item.settlement,
            candidate=item.candidate,
            metrics=item.metrics,
        )
        for item in state.outcomes
        if item.eligible and item.candidate is not None
    )


def _feedback(rnd: round_.Round) -> str | None:
    """What the ledger records of a round that did not pass: the last thing to repair, else why."""
    if rnd.unmeasured is not None:
        return describe_unmeasured(rnd.unmeasured)
    if rnd.repair is not None:
        return rnd.repair.sentence
    if rnd.failure is not None:
        return describe(rnd.failure)
    return None


def _benchmark_outcome(rnd: round_.Round) -> tuple[FrameworkBenchmarkOutcome, float | None]:
    reading = reading_of(rnd.readings, EvidenceKind.BENCHMARK)
    headline: MetricRow | None = None if reading is None else reading.headline()
    if reading is None or headline is None:
        return FrameworkBenchmarkOutcome(
            feedback=None if reading is None else reading.feedback
        ), None
    return (
        FrameworkBenchmarkOutcome(
            feedback=reading.feedback,
            metric_name=headline.name,
            metric_value=headline.value,
            metric_direction=headline.direction,
            metric_unit=headline.unit,
            row={row.name: row.value for row in reading.metrics},
        ),
        headline.value,
    )


def _judge_outcome(rnd: round_.Round, *, replied: bool) -> JudgeOutcome:
    """The round's review as the ledger records it."""
    if rnd.judge_pass is not None:
        return JudgeReviewed("pass" if rnd.judge_pass else "fail")
    if not replied:
        return JudgeSkipped(JudgeSkipReason.UNPARSEABLE_IMPLEMENTATION)
    if rnd.review_skipped:
        return JudgeSkipped(JudgeSkipReason.SPARSE_REVIEW_POLICY)
    return JudgeSkipped(JudgeSkipReason.NOT_REACHED)


def _noted(state: MultiState, notes: tuple[Note, ...]) -> MultiState:
    """The state with a finished child's advisory notes added, keeping the newest."""
    if not notes:
        return state
    return state.model_copy(update={"notes": (*state.notes, *notes)[-NOTE_LIMIT:]})
