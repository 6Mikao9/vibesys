"""Process-family ownership independent of parentage, sessions, and output pipes.

The supervisor stays outside the command's confinement and process group. Its
private owner lifeline is never inherited by the command. Losing that lifeline
and the command leader's exit converge on the same bounded cleanup.
"""

from __future__ import annotations

import signal
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vs_sim.concurrency import Threads


@dataclass(frozen=True, order=True)
class ProcessIdentity:
    """A PID plus a kernel generation; signalling must reject a reused PID."""

    pid: int
    generation: int


class ProcessFamily(Protocol):
    """An owned set that survives fork, exec, reparenting, and setsid.

    ``members`` excludes exited processes, including zombies. ``send`` only
    targets the supplied generation, never a replacement with the same PID.
    Inspection and signal errors raise; an unknown tree is never an empty tree.
    """

    def prepare(self, argv: Sequence[str]) -> tuple[str, ...]:
        """Prepare the command before starting anything in this family."""
        ...

    def members(self) -> frozenset[ProcessIdentity]:
        """Return the live members, whatever their current parent or group."""
        ...

    def send(self, member: ProcessIdentity, number: signal.Signals) -> None:
        """Signal this generation, or do nothing if it already exited."""
        ...


class OwnedChild(Protocol):
    """The command leader; pipes and descendants are not evidence of its lifetime."""

    def poll(self) -> int | None:
        """Return its status, or ``None`` while it runs."""
        ...

    def wait(self, timeout: float) -> int:
        """Reap it within a cleanup bound, raising if it cannot be reaped."""
        ...


class OwnerEvent(StrEnum):
    """What the supervisor's private lifeline observed."""

    ALIVE = "alive"
    LOST = "lost"
    TERMINATE = "terminate"
    INTERRUPT = "interrupt"
    HANGUP = "hangup"


class OwnerLifeline(Protocol):
    """Watches the unconfined owner, which may disappear without forwarding a signal."""

    def receive(self, timeout: float) -> OwnerEvent:
        """Wait for owner loss or a stop request, at most ``timeout`` seconds."""
        ...


class ProcessTreeUnreapedError(RuntimeError):
    """Cleanup could not confirm that every live family member ended."""


CLEANUP_TIMEOUT_SECONDS = 5.0
_POLL_SECONDS = 0.01
_STOP_SIGNALS = {
    OwnerEvent.TERMINATE: signal.SIGTERM,
    OwnerEvent.INTERRUPT: signal.SIGINT,
    OwnerEvent.HANGUP: signal.SIGHUP,
}


def end_process_family(family: ProcessFamily, *, threads: Threads) -> None:
    """Freeze the complete family before killing it, and confirm no live members remain.

    A process can fork between a snapshot and SIGSTOP. Repeat the snapshot until
    every remaining identity has been stopped; only then kill. This also closes
    the fork-during-cleanup race, without trusting environment variables or PGIDs.
    """
    deadline = threads.now() + CLEANUP_TIMEOUT_SECONDS
    stopped: set[ProcessIdentity] = set()
    while members := family.members():
        if threads.now() >= deadline:
            message = f"process family still has live members: {sorted(members)}"
            raise ProcessTreeUnreapedError(message)
        unseen = members - stopped
        if unseen:
            for member in unseen:
                family.send(member, signal.SIGSTOP)
            stopped.update(unseen)
            continue
        for member in members:
            family.send(member, signal.SIGKILL)
        threads.sleep(_POLL_SECONDS)


def supervise_process(
    child: OwnedChild,
    owner: OwnerLifeline,
    family: ProcessFamily,
    *,
    threads: Threads,
) -> int:
    """Own the family until leader exit or owner loss, on every return or exception.

    Stop requests reach every member, including detached children. The owner
    retains its existing grace policy; killing the owner closes the lifeline
    and forces cleanup, even when the command ignores SIGTERM.
    """
    try:
        while child.poll() is None:
            event = owner.receive(_POLL_SECONDS)
            if event is OwnerEvent.LOST:
                break
            if number := _STOP_SIGNALS.get(event):
                for member in family.members():
                    family.send(member, number)
    finally:
        end_process_family(family, threads=threads)
    return child.wait(CLEANUP_TIMEOUT_SECONDS)
