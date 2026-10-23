"""A run that leaves by an exception ends as the user would read it: Ctrl-C is a stop."""

from __future__ import annotations

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vibesys.api._session import unwinding
from vibesys.events import CoreEventType, EventStatus

ERRORS = st.sampled_from(
    [ValueError, RuntimeError, OSError, TimeoutError, SystemExit, GeneratorExit]
)


@pytest.mark.regression
@example(error=RuntimeError, text="KeyboardInterrupt")
@given(error=ERRORS, text=st.text(max_size=30))
def test_only_a_user_interrupt_is_a_stop(error: type[BaseException], text: str) -> None:
    kind, reason, status = unwinding(error(text))
    assert (kind, status) == (CoreEventType.RUN_FAILED, EventStatus.FAILED)
    assert reason == f"{error.__name__}: {text}"


@given(text=st.text(max_size=30))
def test_ctrl_c_stops_the_run_whatever_it_says(text: str) -> None:
    assert unwinding(KeyboardInterrupt(text)) == (
        CoreEventType.STOPPED,
        "Run stopped by interrupt",
        EventStatus.INTERRUPTED,
    )
