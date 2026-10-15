"""``PipedStoppableProcess`` over a scripted ``vs_sim`` piped process, on simulated threads.

The real launcher under the same adapter passes the stoppable-process contract in
``tests/e2e``. Here a scripted process shows what only the adapter decides: how
drained output is joined and normalized, when a wait gives up, and which group
signal each stop signal becomes.
"""

from __future__ import annotations

from io import StringIO
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# test-isolation: the adapter is internal to the command runners; this is its deterministic check.
from vs_sandbox.process_execution import KILL, TERMINATE, PipedStoppableProcess
from vs_sim.api import ProcessTimeoutError
from vs_sim.api.testing import SimThreads, examples

if TYPE_CHECKING:
    from vs_sim.api import Event


class _ScriptedPipedProcess:
    """A piped process with fixed output that exits when *exited* is set."""

    def __init__(self, stdout: str, stderr: str, status: int, exited: Event) -> None:
        self.stdout = StringIO(stdout)
        self.stderr = StringIO(stderr)
        self._status = status
        self._exited = exited
        self.signals: list[str] = []

    def wait(self, timeout: float | None) -> int:
        if not self._exited.wait(timeout):
            raise ProcessTimeoutError(("scripted",), timeout or 0.0)
        return self._status

    def terminate_group(self) -> None:
        self.signals.append("terminate")

    def kill_group(self) -> None:
        self.signals.append("kill")

    def cancel_output(self) -> None:
        raise AssertionError


_TEXT = st.text(alphabet="ab\r\n", max_size=30)


@settings(max_examples=examples(pr=30, nightly=200))
@given(stdout=_TEXT, stderr=_TEXT, status=st.integers(-64, 255))
def test_an_exited_process_reports_all_output_with_universal_newlines(
    stdout: str, stderr: str, status: int
) -> None:
    threads = SimThreads()
    exited = threads.event()
    exited.set()
    process = PipedStoppableProcess(
        _ScriptedPipedProcess(stdout, stderr, status, exited), threads=threads
    )

    finished = threads.run(lambda: process.wait(None))

    def normalized(text: str) -> str:
        return text.replace("\r\n", "\n").replace("\r", "\n")

    assert finished == (normalized(stdout), normalized(stderr))
    assert process.returncode == status


def test_a_wait_on_a_running_process_returns_none_until_it_exits() -> None:
    threads = SimThreads()
    exited = threads.event()
    process = PipedStoppableProcess(_ScriptedPipedProcess("out", "err", 0, exited), threads=threads)

    def case() -> tuple[object, object]:
        early = process.wait(5)
        exited.set()
        return early, process.wait(5)

    assert threads.run(case) == (None, ("out", "err"))


def test_the_returncode_is_unknown_before_the_leader_exits() -> None:
    threads = SimThreads()

    def case() -> None:
        exited = threads.event()
        process = PipedStoppableProcess(_ScriptedPipedProcess("", "", 0, exited), threads=threads)
        with pytest.raises(RuntimeError, match="has not exited"):
            _ = process.returncode
        exited.set()
        process.wait(None)

    threads.run(case)


def test_each_stop_signal_becomes_its_group_signal() -> None:
    threads = SimThreads()
    exited = threads.event()
    exited.set()
    scripted = _ScriptedPipedProcess("", "", 0, exited)
    process = PipedStoppableProcess(scripted, threads=threads)

    process.signal_group(TERMINATE)
    process.signal_group(KILL)
    threads.run(lambda: process.wait(None))

    assert scripted.signals == ["terminate", "kill"]
