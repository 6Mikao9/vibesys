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
from tests.vibesys.orchestration.issue_queue.search._refusals import decision_id
from tests.vibesys.orchestration.issue_queue.search.world import (
    FACTS,
    IssueQueueWorld,
    evaluated,
    implemented,
    issue_queue_config,
    judged,
)

from vibesys.orchestration.issue_queue.search.api import IssueQueueSearch, IssueQueueState
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
    run_view,
)
from vs_core.testing.builders import initial_state
from vs_dst.api import simulate


def _names(decisions: list[Decision]) -> list[str]:
    return [item.root for item in map(decision_id, decisions) if item is not None]


def test_story_one_issue_from_the_bootstrap_to_adoption() -> None:
    """Read, bootstrap, work the issue, review it, evaluate it, measure the tip, propose, finish."""
    world = IssueQueueWorld(
        implementer=[implemented()], judge=[judged()], perf=[evaluated()], max_rounds=1
    )
    run = simulate(world)

    assert _names(list(run.decisions)) == [
        "read.0",
        "bootstrap.0",
        "read.1",
        "issue.1/start",
        "issue.1/claim.0",
        "issue.1/implementer.0~r0",
        "issue.1/implementer.0~t0",
        "issue.1/record.1.0",
        "issue.1/log.implement.1",
        "issue.1/judge.0~r0",
        "issue.1/judge.0~t0",
        "issue.1/verdict.1.0",
        "issue.1/log.review.1",
        "issue.1/settle.0",
        "read.2",
        "perf.2/start",
        "perf.2/perf-evaluator.0~r0",
        "perf.2/perf-evaluator.0~t0",
        "perf.2/log.performance",
        "perf.2/settle.0",
        "read.3",
        "measure.tip",
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
        settings_ = issue_queue_config(max_rounds=2)
        self.search = IssueQueueSearch(settings_)
        self.state, decisions = self.search.init(settings_, FACTS)
        self.view = run_view(initial_state())
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
        if self.finished:
            return  # a finished run takes no more events
        control = ControlInput(control_id=ControlId(root="stop"), action="stop")
        self.state, more = self.search.update(self.state, StopRequested(control=control), self.view)
        self._take(more)

    @invariant()
    def state_round_trips(self) -> None:
        assert IssueQueueState.model_validate_json(self.state.model_dump_json()) == self.state

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
