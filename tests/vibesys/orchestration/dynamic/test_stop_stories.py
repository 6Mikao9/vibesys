"""A stop that arrives while the input is measured: the dynamic search plans nothing and ends.

The input measurement is the first thing the search waits for. A stop that finds it open must
neither wait for its end to start the first planner turn nor start any work after it: the run
ends cancelled. (What the host and core do with the measurement's job is theirs: see
``tests/system/timing/test_stop_during_work.py`` and ``libs/vs-core/tests/test_cancel_stop_run_jobs.py``.)
"""

from __future__ import annotations

import pytest
from tests.vibesys.orchestration.dynamic._host import PLANNER_LABEL, begin, config

from vs_core.api import Finish, Measure, RequestTurn

MODES = pytest.mark.parametrize("restarting", [False, True], ids=["live", "restart-every-step"])


@MODES
def test_a_stop_while_the_input_is_measured_starts_no_planner_turn_and_ends_cancelled(
    *, restarting: bool
) -> None:
    host = begin(config(), restarting=restarting)
    assert host.open(Measure), "the input measurement is the first wait of the run"

    host.run.stop()
    host.drain()

    assert isinstance(host.run.finished, Finish)
    assert host.run.finished.result.outcome == "cancelled"
    assert host.planner_calls == 0
    assert not host.open(RequestTurn, label=PLANNER_LABEL)
    assert not host.opened
