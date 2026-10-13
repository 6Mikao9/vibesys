"""C2: crash and replay at the durable boundaries of a run, for every registered strategy.

The host dies just before or just after the k-th commit; a new host with a new search
instance restarts over the store the dead one left, and the executors keep their world. The run
must reach the result an uninterrupted run reaches, publish an equal record, apart from observation times, and execute
no request identity twice. The boundaries are the commits of the subject's smallest full run.

The crash points tried are the suite's one setting (`--crash-points`): CI samples, the
nightly job tries every commit (the policy of #1491). Trying every commit of the largest subject
takes longer than one test may (`faulthandler_timeout` in `pyproject.toml`), so the exhaustive
setting splits each sweep into interleaved parts that the workers share.
"""

from __future__ import annotations

import pytest
from tests.composition._loop.dst_world import SubjectWorld
from tests.composition._loop.subjects import SUBJECTS

from vibesys.orchestration.dynamic.search.api import RunSnapshot
from vs_core.api import FlowDriver, FlowState, StrategyState
from vs_dst.api import EVERY_POINT, CrashKind, SimulatedRun, crash_point_policy, simulate

SUBJECT_NAMES = sorted(name for name, subject in SUBJECTS.items() if subject.sweeps_crashes)


def _published(state: StrategyState) -> object:
    """What the search published of its record, without the times its observations were made.

    A restart is late by the host's lease, so the observations it makes are stamped later;
    that changes when the search learned a fact, not what it decided.
    """
    assert isinstance(state, FlowState)
    record = FlowDriver.published(state, RunSnapshot)
    return None if record is None else _untimed(record.model_dump(mode="json"))


def _untimed(value: object) -> object:
    if isinstance(value, dict):
        return {key: _untimed(item) for key, item in value.items() if key != "observed_at"}
    if isinstance(value, list):
        return [_untimed(item) for item in value]
    return value


def _lapsed_while_down(recovered: SimulatedRun) -> bool:
    """Whether the run stalled on a request whose time limit passed while no host was up.

    A restart comes a lease after the crash, later than a request's own limit (a prompt
    renders within five minutes). Core marks such a request blocked and cannot close the
    run over it; `RESOLUTIONS` in `vs_core/_waits.py` names that gap. Every other way of not
    finishing still fails the sweep.
    """
    return not recovered.finished and ":blocked" in str(recovered.halted)


PARTS = 4 if crash_point_policy() == EVERY_POINT else 1


@pytest.mark.parametrize("name", SUBJECT_NAMES)
@pytest.mark.parametrize("kind", list(CrashKind))
@pytest.mark.parametrize("part", range(PARTS))
def test_c2_a_restart_after_any_crash_finishes_the_same_run(
    name: str, kind: CrashKind, part: int
) -> None:
    subject = SUBJECTS[name]
    normal = simulate(SubjectWorld(subject, subject.smallest_world()))
    assert normal.finished

    for crash in normal.crash_points(kind)[part::PARTS]:
        recovered = normal.rerun_with_crash(crash)
        if _lapsed_while_down(recovered):
            continue
        assert recovered.finished, crash
        assert recovered.result == normal.result, crash
        assert _published(recovered.state) == _published(normal.state), crash
        assert recovered.no_effect_ran_twice(), crash
