"""Detached descendants retain ownership on every process-lifetime exit path."""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_sim.api import (
    OwnerEvent,
    ProcessTreeUnreapedError,
    end_process_family,
    supervise_process,
)
from vs_sim.api.testing import FakeOwnedChild, FakeProcessFamily, SimThreads

if TYPE_CHECKING:
    from vs_sim.api import ProcessIdentity


class _Owner:
    def __init__(self, event: OwnerEvent) -> None:
        self._event = event

    def receive(self, _timeout: float) -> OwnerEvent:
        return self._event


@pytest.mark.regression
@pytest.mark.parametrize("ending", ["complete", "timeout", "cancel", "owner_killed"])
@given(detach=st.lists(st.booleans(), min_size=1, max_size=20))
@example(detach=[False, True, True])
def test_descendants_never_outlive_the_command_or_turn(ending: str, detach: list[bool]) -> None:
    threads = SimThreads()
    family = FakeProcessFamily()
    leader = family.spawn()
    parent = leader
    for new_session in detach:
        child = family.spawn(parent)
        if new_session:
            family.setsid(child)
        if parent != leader:
            family.exit(parent)  # double fork: old ancestry and groups cannot own it
        parent = child
    if ending == "complete":
        family.exit(leader, 7)
    event = OwnerEvent.TERMINATE if ending == "cancel" else OwnerEvent.LOST

    status = threads.run(
        lambda: supervise_process(
            FakeOwnedChild(family, leader), _Owner(event), family, threads=threads
        )
    )

    assert not family.members()
    assert status == (
        7 if ending == "complete" else -signal.SIGTERM if ending == "cancel" else -signal.SIGKILL
    )


def test_cleanup_includes_a_child_forked_between_inspection_and_stop() -> None:
    threads = SimThreads()

    def fork(family: FakeProcessFamily, parent: ProcessIdentity) -> None:
        child = family.spawn(parent)
        family.setsid(child)
        family.exit(parent)

    family = FakeProcessFamily(before_stop=fork)
    family.spawn()

    threads.run(lambda: end_process_family(family, threads=threads))

    assert not family.members()
    assert len({member for member, _ in family.sent}) == 2


def test_cleanup_failure_is_explicit_and_bounded_on_the_simulated_clock() -> None:
    threads = SimThreads()
    family = FakeProcessFamily(refuse_kill=True)
    family.spawn()

    with pytest.raises(ProcessTreeUnreapedError, match="live members"):
        threads.run(lambda: end_process_family(family, threads=threads))


def test_stale_generation_does_not_signal_a_replacement_or_another_family() -> None:
    threads = SimThreads()
    first = FakeProcessFamily()
    second = FakeProcessFamily()
    member = first.spawn()
    second.spawn()
    first.exit(member)

    threads.run(lambda: end_process_family(first, threads=threads))

    assert not first.sent
    assert len(second.members()) == 1


def test_unexpected_owner_error_still_releases_the_entire_family() -> None:
    class BrokenOwner:
        def receive(self, _timeout: float) -> OwnerEvent:
            message = "owner inspection failed"
            raise OSError(message)

    threads = SimThreads()
    family = FakeProcessFamily()
    leader = family.spawn()
    family.setsid(family.spawn(leader))

    with pytest.raises(OSError, match="owner inspection failed"):
        threads.run(
            lambda: supervise_process(
                FakeOwnedChild(family, leader), BrokenOwner(), family, threads=threads
            )
        )

    assert not family.members()
