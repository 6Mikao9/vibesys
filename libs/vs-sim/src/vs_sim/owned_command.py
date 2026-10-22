"""Native command entrypoint with a supervisor that survives loss of its owner.

The relay is the process the caller owns. A separate-session keeper receives
its private pipe (never inherited by the actual command). Even SIGKILL of the
relay closes the pipe and asks the keeper to end the complete process family.
"""

from __future__ import annotations

import fcntl
import os
import select
import signal
import subprocess
import sys
from contextlib import suppress
from typing import TYPE_CHECKING

from vs_sim.concurrency import OsThreads
from vs_sim.darwin_process_family import DarwinProcessFamily
from vs_sim.linux_process_family import LinuxProcessFamily
from vs_sim.process_family import OwnerEvent, supervise_process

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from types import FrameType

_STDERR_FD = 2
_FIRST_PRIVATE_FD = 3

_SIGNALS = {
    signal.SIGTERM: OwnerEvent.TERMINATE,
    signal.SIGINT: OwnerEvent.INTERRUPT,
    signal.SIGHUP: OwnerEvent.HANGUP,
}


def owned_command(argv: Sequence[str]) -> tuple[str, ...]:
    """Return argv that owns all descendants until completion, timeout, or owner loss.

    This is pure launch composition. Native execution is confined to this
    module's entrypoint, and deterministic callers can inspect the returned argv.
    """
    return (sys.executable, "-m", "vs_sim.owned_command", "--", *argv)


class _PipeOwnerLifeline:
    def __init__(self, descriptor: int) -> None:
        self._descriptor = descriptor

    def receive(self, timeout: float) -> OwnerEvent:
        ready, _, _ = select.select([self._descriptor], [], [], timeout)
        if not ready:
            return OwnerEvent.ALIVE
        request = os.read(self._descriptor, 1)
        return _SIGNALS[signal.Signals(request[0])] if request else OwnerEvent.LOST


def _keep(descriptor: int, argv: Sequence[str]) -> int:
    """Own one process family; the owner pipe is closed on every exit."""
    try:
        owner = _PipeOwnerLifeline(descriptor)
        family = DarwinProcessFamily() if sys.platform == "darwin" else LinuxProcessFamily()
        command = family.prepare(argv)
        initial = owner.receive(0)
        if initial is OwnerEvent.LOST:
            return 125
        if initial is not OwnerEvent.ALIVE:
            return -next(number for number, event in _SIGNALS.items() if event is initial)
        child = subprocess.Popen(command, start_new_session=True)  # noqa: S603  # lint-waiver: LW-731025 [S603]; this adapter executes the caller's argv without a shell. Re-parsing it or adding a shell would duplicate caller validation and introduce quoting hazards.
        return supervise_process(child, owner, family, threads=OsThreads())
    finally:
        os.close(descriptor)


def _lifeline_pipe() -> tuple[int, int]:
    reader, writer = os.pipe()
    # Keep the private pipe off stdin/stdout/stderr even when the caller has
    # closed one of them. Popen otherwise inherits it as a standard stream.
    endpoints: list[int] = []
    for descriptor in (reader, writer):
        if descriptor <= _STDERR_FD:
            replacement = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, _FIRST_PRIVATE_FD)
            os.close(descriptor)
            endpoints.append(replacement)
        else:
            endpoints.append(descriptor)
    return endpoints[0], endpoints[1]


def _relay(argv: Sequence[str]) -> int:
    reader, writer = _lifeline_pipe()
    previous: dict[signal.Signals, signal.Handlers | Callable[..., object] | int | None] = {}
    try:
        os.set_blocking(writer, False)
        keeper = subprocess.Popen(  # noqa: S603  # lint-waiver: LW-731026 [S603]; the fixed Python entrypoint forwards the caller's argument vector. A shell or validation here would add quoting hazards or duplicate the command boundary.
            [sys.executable, "-m", "vs_sim.owned_command", "--keeper", str(reader), "--", *argv],
            pass_fds=(reader,),
            start_new_session=True,
        )
        os.close(reader)
        reader = -1

        def forward(number: int, _frame: FrameType | None) -> None:
            # The pipe identifies its owner without a PID-reuse race. A full
            # pipe already contains a stop request; never block a handler.
            with suppress(BlockingIOError):
                os.write(writer, bytes([number]))

        for number in _SIGNALS:
            previous[number] = signal.signal(number, forward)
        return keeper.wait()
    finally:
        for number, handler in previous.items():
            if handler is not None:
                signal.signal(number, handler)
        if reader >= 0:
            os.close(reader)
        os.close(writer)


def main() -> None:
    """Run only as a child entrypoint; never install handlers in the application's host."""
    args = sys.argv[1:]
    if args and args[0] == "--keeper":
        status = _keep(int(args[1]), args[3:])
    elif args and args[0] == "--":
        status = _relay(args[1:])
    else:
        message = "expected -- command or --keeper descriptor -- command"
        raise ValueError(message)
    if status < 0:
        number = signal.Signals(-status)
        signal.signal(number, signal.SIG_DFL)
        os.kill(os.getpid(), number)
    raise SystemExit(status)


if __name__ == "__main__":
    main()
