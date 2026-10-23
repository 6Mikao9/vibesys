"""A restart after a code change resumes under the widths the operator now sets.

The root resumes from the config its last checkpoint holds, which is the config the run started
under. An operator who resumes with another ``--max-in-flight`` or ``--max-backlog`` (and core's
limits planned from them) must see the run take them: the first free slot after the restart is
the new width's, not the recorded one's. The settings a resume may set are
`DynamicConfig.ADJUSTABLE_ON_RESUME`; every other setting resumes as recorded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._host import PLANNER_LABEL, Host, begin, config
from tests.vibesys.orchestration.dynamic._replies import implement, plan_reply
from tests.vibesys.orchestration.dynamic._values import config as value_config
from tests.vibesys.orchestration.dynamic._values import run_record

from vibesys.orchestration.dynamic.search.api import DynamicConfig, DynamicResume, dynamic
from vs_core.api import RequestTurn
from vs_core.api.testing import code_changed
from vs_sim.api.testing import examples

if TYPE_CHECKING:
    from vs_core.api.testing import Asked


def _plan_one_hypothesis(host: Host) -> Asked:
    """Plan ``h1`` and drive it to its implementer's open turn; the turn."""
    host.run.reply_raw(host.run.expect_turn(role=PLANNER_LABEL), plan_reply(implement("h1")))
    for _ in range(50):
        if turns := host.open(RequestTurn, label="implementer"):
            return turns[0]
        waits = [item for item in host.run.open if item.until is not None]
        host.answer(waits[0])
    message = f"h1 never asked its implementer: {host.run.timeline()}"
    raise AssertionError(message)


def _resume_under(host: Host, resumed: DynamicConfig) -> None:
    """The operator resumes with ``resumed`` after a code change; core's limits follow from it."""
    limits = host.run.view.current().limits
    host.run.view.changes["limits"] = limits.model_copy(
        update={"max_attempts": resumed.start_budget, "max_parallel": resumed.admission_limit}
    )
    host.run.swap(code_changed(dynamic), resumed)


@pytest.mark.regression
@settings(
    max_examples=examples(pr=3, nightly=12),
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@example(now=2)
@given(now=st.integers(1, 3))
def test_a_restart_plans_into_the_free_slot_of_the_width_the_operator_now_sets(now: int) -> None:
    """One slot, taken by ``h1``'s implementer: the planner waits. After a restart under a wider
    ``--max-in-flight`` it is asked for the slots that opened; under the same width it still waits."""
    host = begin(config(max_rounds=4, max_in_flight=1), restarting=False)
    implementer = _plan_one_hypothesis(host)
    assert not host.open(RequestTurn, label=PLANNER_LABEL)

    _resume_under(host, config(max_rounds=4, max_in_flight=now))
    host.run.reply_raw(implementer, "not json")  # the first event the new code sees

    assert bool(host.open(RequestTurn, label=PLANNER_LABEL)) == (now > 1), host.run.timeline()


@settings(max_examples=examples(pr=20, nightly=200), deadline=None)
@given(
    recorded=st.fixed_dictionaries(
        {
            "max_rounds": st.integers(1, 50),
            "max_in_flight": st.integers(1, 8),
            "max_backlog": st.none() | st.integers(0, 8),
            "judge_every": st.integers(1, 5),
            "max_unmeasured_turns": st.integers(1, 8),
            "pick": st.sampled_from(["newest", "ranked"]),
        }
    ),
    present=st.fixed_dictionaries(
        {
            "max_rounds": st.integers(1, 50),
            "max_in_flight": st.integers(1, 8),
            "max_backlog": st.none() | st.integers(0, 8),
            "judge_every": st.integers(1, 5),
            "max_unmeasured_turns": st.integers(1, 8),
            "pick": st.sampled_from(["newest", "ranked"]),
        }
    ),
)
def test_a_resumed_config_takes_exactly_the_settings_an_operator_may_set(
    recorded: dict[str, object], present: dict[str, object]
) -> None:
    """The adjustable settings come from the config of the present resume and nothing else does."""
    old, new = DynamicConfig.model_validate(recorded), DynamicConfig.model_validate(present)

    resumed = old.resumed_under(new)

    for name in DynamicConfig.model_fields:
        expected = new if name in DynamicConfig.ADJUSTABLE_ON_RESUME else old
        assert getattr(resumed, name) == getattr(expected, name), name
    assert resumed.admission_limit == new.admission_limit


def test_a_checkpoint_resumed_under_a_config_keeps_its_record() -> None:
    record = run_record(value_config(max_rounds=3, max_in_flight=1))
    checkpoint = DynamicResume(config=record.config, record=record.state())

    resumed = checkpoint.resumed_under(value_config(max_rounds=5, max_in_flight=2, max_backlog=1))

    assert resumed.record == checkpoint.record
    assert (resumed.config.max_rounds, resumed.config.max_in_flight, resumed.config.backlog) == (
        5,
        2,
        1,
    )
