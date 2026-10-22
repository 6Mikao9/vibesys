"""Piped native-command simulation using the same supervisor as production.

An unwrapped command models ordinary process-group semantics. A wrapped one
runs the real supervisor in process, on simulated threads. This lets callers
verify that their launch boundary actually selects process-family ownership.
"""

from __future__ import annotations

import signal
from typing import TYPE_CHECKING

from vs_sim.fake_piped_process import ScriptedPipedProcess
from vs_sim.fake_process_family import FakeOwnedChild, FakeProcessFamily
from vs_sim.owned_command import owned_command
from vs_sim.process_family import OwnerEvent, supervise_process

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from vs_sim.concurrency import Threads
    from vs_sim.process_family import ProcessIdentity
    from vs_sim.processes import PipedProcess


class _Owner:
    def __init__(self, threads: Threads) -> None:
        self._condition = threads.condition()
        self._pending: list[OwnerEvent] = []

    def send(self, event: OwnerEvent) -> None:
        with self._condition:
            self._pending.append(event)
            self._condition.notify_all()

    def receive(self, timeout: float) -> OwnerEvent:
        with self._condition:
            self._condition.wait_for(lambda: bool(self._pending), timeout)
            return self._pending.pop(0) if self._pending else OwnerEvent.ALIVE


class _FamilyPipedProcess(ScriptedPipedProcess):
    def __init__(
        self,
        threads: Threads,
        family: FakeProcessFamily,
        leader: ProcessIdentity,
        *,
        merge_stderr: bool,
    ) -> None:
        super().__init__(threads, merge_stderr=merge_stderr)
        self._family = family
        self._leader = leader
        self.owner: _Owner | None = None

    def terminate_group(self) -> None:
        if self.owner is not None:
            self.owner.send(OwnerEvent.TERMINATE)
        else:
            self._family.signal_group(self._leader, signal.SIGTERM)
            super().terminate_group()

    def kill_group(self) -> None:
        if self.owner is not None:
            self.exit(-signal.SIGKILL, close_output=False)
            self.owner.send(OwnerEvent.LOST)
        else:
            self._family.signal_group(self._leader, signal.SIGKILL)
            super().kill_group()


class FakeFamilyPipedProcessLauncher:
    """Run a scripted process family, honoring the production ownership launch prefix."""

    def __init__(
        self,
        threads: Threads,
        program: Callable[[FakeProcessFamily, ProcessIdentity], None],
    ) -> None:
        """Run ``program`` at launch; it forks descendants and may exit its leader."""
        self._threads = threads
        self._program = program
        self.family = FakeProcessFamily()

    def start(
        self,
        argv: tuple[str, ...],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        merge_stderr: bool = False,
    ) -> PipedProcess:
        """Execute the scripted leader, with or without its private supervisor."""
        del cwd, env
        family = FakeProcessFamily()
        self.family = family
        leader = family.spawn()
        relay = _FamilyPipedProcess(self._threads, family, leader, merge_stderr=merge_stderr)
        self._program(family, leader)
        prefix = owned_command(())
        if argv[: len(prefix)] == prefix:
            owner = _Owner(self._threads)
            relay.owner = owner

            def supervise() -> None:
                status = supervise_process(
                    FakeOwnedChild(family, leader), owner, family, threads=self._threads
                )
                relay.exit(status)

            self._threads.spawn(supervise, name="fake-command-supervisor")
        elif (status := family.status(leader)) is not None:
            relay.exit(status)
        return relay
