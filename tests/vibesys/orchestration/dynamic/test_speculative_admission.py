"""A busy evaluator imposes no default ceiling on speculative work or corrupts its diff base."""

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic._values import (
    BASE,
    combine_entry,
    portfolio,
    revision,
    run_record,
    workstream_start,
)

from vibesys.orchestration.dynamic.search.api import (
    DynamicConfig,
    Scheduled,
    Submission,
    WorkKind,
    budget,
    validate,
)


@pytest.mark.regression
@example(waiting=33)
@given(waiting=st.integers(33, 100))
def test_the_default_backlog_parks_every_waiter_within_the_runs_work_budget(waiting: int) -> None:
    config = DynamicConfig(max_rounds=waiting + 1, max_in_flight=1)
    run = run_record(config)
    for i in range(waiting):
        start = workstream_start(sequence=i + 1, work_id=f"h{i}")
        run.running[start.key] = start
        run.submit(
            Submission(
                key=start.key,
                work_id=start.plan.work_id,
                kind=WorkKind.IMPLEMENT,
                candidate=revision(f"c{i}"),
                parent=BASE,
                at=0,
            )
        )
    assert len(run.parked) == waiting
    assert run.capacity() == 1
    assert budget(config).parallel >= waiting + 1


@pytest.mark.regression
@example(caught_up=True)
@given(caught_up=st.booleans())
def test_a_waiting_combine_part_uses_the_main_it_caught_up_with(*, caught_up: bool) -> None:
    run = run_record()
    main = revision("new-main")
    for i in range(2):
        start = workstream_start(sequence=i + 1, work_id=f"h{i}").model_copy(
            update={"parent_main": BASE, "rebase_onto": main if caught_up else None}
        )
        run.running[start.key] = start
        run.submit(
            Submission(
                key=start.key,
                work_id=start.plan.work_id,
                kind=WorkKind.IMPLEMENT,
                candidate=revision(f"c{i}"),
                parent=BASE,
                at=0,
            )
        )
    check = validate(
        portfolio(combine_entry("k", ("h0", "c0"), ("h1", "c1"))),
        run.world().ledger,
        run.offered(),
        capacity=1,
        offer=None,
    )
    assert check.valid
    run.planned(Scheduled(check=check))
    (combined,) = run.ready_to_start(1)
    assert all(part.built_on == (main if caught_up else BASE) for part in combined.combine)
