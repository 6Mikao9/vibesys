"""A process-family simulator that retains ownership across parent and session changes."""

from __future__ import annotations

import signal
from dataclasses import dataclass
from typing import TYPE_CHECKING

from vs_sim.process_family import ProcessIdentity

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


@dataclass
class _Member:
    parent: ProcessIdentity | None
    group: int
    status: int | None = None
    stopped: bool = False


class FakeProcessFamily:
    """Own forked children by family, rather than their current group or ancestry.

    ``before_stop`` scripts a fork racing the first SIGSTOP, and ``refuse_kill``
    models a kernel that accepts signals but never confirms exit. Both also
    exercise the production cleanup loop's unhappy paths without real signals.
    """

    def __init__(
        self,
        *,
        before_stop: Callable[[FakeProcessFamily, ProcessIdentity], None] | None = None,
        refuse_kill: bool = False,
    ) -> None:
        """Start with no processes and no signal failures."""
        self._members: dict[ProcessIdentity, _Member] = {}
        self._next_pid = 100
        self._before_stop = before_stop
        self._refuse_kill = refuse_kill
        self.sent: list[tuple[ProcessIdentity, signal.Signals]] = []

    def prepare(self, argv: Sequence[str]) -> tuple[str, ...]:
        """The simulator needs no native launch prefix."""
        return tuple(argv)

    def spawn(self, parent: ProcessIdentity | None = None) -> ProcessIdentity:
        """Start a member, inheriting its live parent's family and process group."""
        if parent is not None and self._members[parent].status is not None:
            message = "an exited process cannot fork"
            raise RuntimeError(message)
        member = ProcessIdentity(self._next_pid, 1)
        self._next_pid += 1
        group = member.pid if parent is None else self._members[parent].group
        self._members[member] = _Member(parent, group)
        return member

    def setsid(self, member: ProcessIdentity) -> None:
        """Leave the old process group, retaining the inherited family."""
        self._members[member].group = member.pid

    def exit(self, member: ProcessIdentity, status: int = 0) -> None:
        """End a member and reparent its children, retaining their family."""
        self._members[member].status = status
        for state in self._members.values():
            if state.parent == member:
                state.parent = None

    def status(self, member: ProcessIdentity) -> int | None:
        """The leader status, independently of its descendants and output pipes."""
        return self._members[member].status

    def members(self) -> frozenset[ProcessIdentity]:
        """Return every live generation, including detached, reparented descendants."""
        return frozenset(member for member, state in self._members.items() if state.status is None)

    def send(self, member: ProcessIdentity, number: signal.Signals) -> None:
        """Signal only a live generation belonging to this family."""
        state = self._members.get(member)
        if state is None or state.status is not None:
            return
        self.sent.append((member, number))
        if number == signal.SIGSTOP:
            if self._before_stop is not None:
                before, self._before_stop = self._before_stop, None
                before(self, member)
            state.stopped = True
        elif (number == signal.SIGKILL and not self._refuse_kill) or number in (
            signal.SIGTERM,
            signal.SIGINT,
            signal.SIGHUP,
        ):
            self.exit(member, -number)

    def signal_group(self, leader: ProcessIdentity, number: signal.Signals) -> None:
        """Model the old group-only cleanup, which misses setsid children."""
        group = self._members[leader].group
        for member in self.members():
            if self._members[member].group == group:
                self.send(member, number)


class FakeOwnedChild:
    """A command leader in a :class:`FakeProcessFamily`."""

    def __init__(self, family: FakeProcessFamily, leader: ProcessIdentity) -> None:
        """Observe an already-started simulated leader."""
        self._family = family
        self._leader = leader

    def poll(self) -> int | None:
        """Return the status independently of output pipes or descendants."""
        return self._family.status(self._leader)

    def wait(self, timeout: float) -> int:
        """Reap a leader cleanup already ended; a live leader is an error."""
        status = self.poll()
        if status is None:
            raise TimeoutError(timeout)
        return status
