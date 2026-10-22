"""An agent that cannot start ends the planning call on that turn.

A process that exits before a turn starts used to be recorded as unknown, which
the planner retried as a lost turn. The call now ends, and the run's failure
names the CLI's own error.
"""

from __future__ import annotations

from tests.vibesys.orchestration.dynamic._host import PLANNER_LABEL, begin, config

from vs_core.api import Finish, TurnFailureKind

_CLI = "failed to initialize in-process app-server client: Operation not permitted"


def test_a_planner_that_cannot_start_is_not_asked_again() -> None:
    host = begin(config(max_in_flight=1, max_rounds=2), restarting=False)
    host.drain_until_turn(PLANNER_LABEL)
    turn = host.run.expect_turn(role=PLANNER_LABEL)
    host.run.fail_turn(
        turn,
        TurnFailureKind.LAUNCH_FAILED,
        detail=f"agent launch failed: {_CLI}",
    )

    for _ in range(40):
        if host.run.finished is not None:
            break
        if any(item.label == PLANNER_LABEL for item in host.run.open):
            message = "the planner was asked again after a launch failure"
            raise AssertionError(message)
        if not host.step(0):
            break

    finish = host.run.finished
    assert isinstance(finish, Finish)
    assert finish.result.outcome == "failure"
    assert _CLI in finish.result.reason
