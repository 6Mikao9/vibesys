"""A whole run told in order, and the search driven by arbitrary endings of its own decisions.

The story pins the sequence of decisions of one successful run. The state machine then ends
decisions in every way core may (timed out, refused) and delivers a stop at any time; whatever
the order, the state stays a valid persisted value, the search never asks the same decision id
twice at once, and it never goes quiet without having finished.
"""

from __future__ import annotations

from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule
from tests.vibesys.orchestration.single.search._refusals import decision_id
from tests.vibesys.orchestration.single.search._views import empty_view
from tests.vibesys.orchestration.single.search.world import FACTS, SingleWorld, single_config

from vibesys.orchestration.single.search.api import SingleSearch, SingleState
from vs_core.api import (
    ControlId,
    ControlInput,
    Decision,
    DecisionId,
    Failed,
    FailureReason,
    Finish,
    RejectionCode,
    StopRequested,
    Wait,
)
from vs_dst.api import simulate


def _names(decisions: list[Decision]) -> list[str]:
    return [item.root for item in map(decision_id, decisions) if item is not None]


def _round_story(number: int) -> list[str]:
    """Design the hypothesis, open the attempt, one implementer turn, measure, decode, settle."""
    return [
        f"design/plan/{number}.0~r0",
        f"design/plan/{number}.0~t0",
        f"round.{number}/start",
        f"round.{number}/implementer.0~r0",
        f"round.{number}/implementer.0~t0",
        f"round.{number}/measure.0",
        f"round.{number}/interpret",
        f"round.{number}/settle",
    ]


def test_story_two_rounds_from_baseline_to_adoption() -> None:
    """Measure the baseline, then per round: plan, start, implement, measure, settle; propose."""
    run = simulate(SingleWorld())

    assert _names(list(run.decisions)) == [
        "baseline/measure.0",
        "baseline/interpret",
        *_round_story(1),
        *_round_story(2),
        "propose",
        "finish",
    ]


ENDINGS = st.sampled_from(
    [
        Failed(reason=FailureReason.TIMED_OUT, limit_at=1.0),
        *(Failed(reason=FailureReason.REFUSED, cause=code) for code in RejectionCode),
    ]
)


class SearchUnderArbitraryEndings(RuleBasedStateMachine):
    """Decisions end by timeout or refusal, in any order, and a stop may land at any time."""

    def __init__(self) -> None:
        super().__init__()
        settings_ = single_config(max_rounds=2)
        self.search = SingleSearch(settings_)
        self.state, decisions = self.search.init(settings_, FACTS)
        self.view = empty_view(FACTS)
        self.open: dict[DecisionId, Decision] = {}
        self.finished = False
        self._take(decisions)

    def _take(self, decisions: list[Decision]) -> None:
        for item in decisions:
            if isinstance(item, Finish):
                self.finished = True
                continue
            identity = decision_id(item)
            if identity is not None and isinstance(item, Wait):
                assert identity not in self.open, f"{identity.root} asked twice at once"
                self.open[identity] = item

    @precondition(lambda self: bool(self.open) and not self.finished)
    @rule(data=st.data(), ending=ENDINGS)
    def end_a_decision(self, data: st.DataObject, ending: Failed) -> None:
        identity = data.draw(st.sampled_from(sorted(self.open, key=lambda key: key.root)))
        del self.open[identity]
        self.state, more = self.search.update(
            self.state, ending.model_copy(update={"decision": identity}), self.view
        )
        self._take(more)

    @rule()
    def stop(self) -> None:
        control = ControlInput(control_id=ControlId(root="stop"), action="stop")
        before = self.state
        self.state, more = self.search.update(self.state, StopRequested(control=control), self.view)
        if self.finished:
            assert (self.state, more) == (before, []), "a finished run took an event"
        self._take(more)

    @precondition(lambda self: self.finished and bool(self.open))
    @rule(data=st.data(), ending=ENDINGS)
    def end_after_finish(self, data: st.DataObject, ending: Failed) -> None:
        identity = data.draw(st.sampled_from(sorted(self.open, key=lambda key: key.root)))
        before = self.state
        self.state, more = self.search.update(
            self.state, ending.model_copy(update={"decision": identity}), self.view
        )
        assert more == [] or all(isinstance(item, Finish) for item in more)
        assert self.state.run.kind == "finished"
        assert before.run.kind == "finished"

    @invariant()
    def state_round_trips(self) -> None:
        assert SingleState.model_validate_json(self.state.model_dump_json()) == self.state

    @invariant()
    def never_quiet_without_finishing(self) -> None:
        assert self.finished or self.open, "the search holds no decision and has not finished"


SearchUnderArbitraryEndings.TestCase.settings = settings(
    max_examples=40,
    stateful_step_count=25,
    deadline=None,
    derandomize=True,
    database=None,
    suppress_health_check=[HealthCheck.too_slow],
)
TestSearchUnderArbitraryEndings = SearchUnderArbitraryEndings.TestCase
