"""Linux subreaper ownership: double-forked children stay with their supervisor."""

from __future__ import annotations

import ctypes
import os
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING

from vs_sim.process_family import ProcessIdentity
from vs_sim.signals import _pidfd_calls

if TYPE_CHECKING:
    import signal
    from collections.abc import Sequence

_SUBREAPER = 36  # PR_SET_CHILD_SUBREAPER, linux/prctl.h


class LinuxProcessFamily:
    """A dedicated supervisor's descendants, including every adopted orphan."""

    def __init__(self) -> None:
        """Become a subreaper before launching; never change the application's host process."""
        libc = ctypes.CDLL(None, use_errno=True)
        if libc.prctl(_SUBREAPER, 1, 0, 0, 0) < 0:
            raise OSError(ctypes.get_errno(), "cannot acquire process-family ownership")
        self._root = os.getpid()
        self._open_pidfd, self._send_pidfd = _pidfd_calls(direct=False)

    def prepare(self, argv: Sequence[str]) -> tuple[str, ...]:
        """Children need no marker: the kernel reparents orphans to this supervisor."""
        return tuple(argv)

    def _children(self, pid: int) -> set[int]:
        children: set[int] = set()
        with suppress(FileNotFoundError, ProcessLookupError):
            for task in Path(f"/proc/{pid}/task").iterdir():
                with suppress(FileNotFoundError, ProcessLookupError):
                    children.update(map(int, (task / "children").read_text().split()))
        return children

    def _identity(self, pid: int) -> ProcessIdentity | None:
        with suppress(FileNotFoundError, ProcessLookupError):
            text = Path(f"/proc/{pid}/stat").read_text()
            fields = text[text.rindex(")") + 2 :].split()
            if fields[0] not in ("Z", "X"):
                return ProcessIdentity(pid, int(fields[19]))  # starttime, field 22
        return None

    def members(self) -> frozenset[ProcessIdentity]:
        """Walk descendants, revisiting the root to include concurrent orphan adoption."""
        visited: set[int] = set()
        pending = self._children(self._root)
        members: set[ProcessIdentity] = set()
        while pending:
            pid = pending.pop()
            if pid in visited:
                continue
            visited.add(pid)
            if member := self._identity(pid):
                members.add(member)
            pending.update(self._children(pid) - visited)
            # An intermediate parent may have exited while its children were read.
            pending.update(self._children(self._root) - visited)
        return frozenset(members)

    def send(self, member: ProcessIdentity, number: signal.Signals) -> None:
        """Pin the kernel process before revalidating its generation."""
        try:
            descriptor = self._open_pidfd(member.pid)
        except ProcessLookupError:
            return
        try:
            if self._identity(member.pid) == member:
                with suppress(ProcessLookupError):
                    self._send_pidfd(descriptor, number)
        finally:
            os.close(descriptor)
