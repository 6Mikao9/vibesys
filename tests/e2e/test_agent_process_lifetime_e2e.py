"""CPU-only wiring checks: real detached children under the rendered Seatbelt policy."""

from __future__ import annotations

import json
import os
import select
import signal
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from vs_sandbox.api import LocalShellRunner, SeatbeltSandbox
from vs_sim.api import (
    DISCARD,
    OsThreads,
    SubprocessPipedProcessLauncher,
    SubprocessProbe,
    owned_command,
)
from vs_sim.api.testing import HANG_GUARD_S

if TYPE_CHECKING:
    from pathlib import Path

# Real processes are intentional in this e2e tier. The command reports all PIDs
# before the test lets it end; no sleep decides when it is safe to cancel it.
_PROGRAM = """
import json, os, signal, subprocess, sys
children = []
for detached in (False, True):
    child = subprocess.Popen(['/bin/sleep', '600'], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=detached, env={})
    children.append(child.pid)
reader, writer = os.pipe()
intermediate = os.fork()
if intermediate == 0:
    os.close(reader)
    os.setsid()
    if os.fork():
        os._exit(0)
    os.write(writer, str(os.getpid()).encode())
    os.close(writer)
    os.execve('/bin/sleep', ['sleep', '600'], {})
os.close(writer)
children.append(int(os.read(reader, 64)))
os.close(reader)
os.waitpid(intermediate, 0)
print(json.dumps(children), flush=True)
if sys.argv[1] == 'signal':
    own = subprocess.Popen(['/bin/sleep', '600'], start_new_session=True)
    os.killpg(own.pid, signal.SIGTERM)
    assert own.wait(timeout=5) == -signal.SIGTERM
    try:
        os.kill(int(sys.argv[2]), signal.SIGTERM)
    except PermissionError:
        pass
    else:
        raise AssertionError('confined command could signal an unconfined process')
if sys.argv[1] == 'hold':
    sys.stdin.buffer.read(1)
sys.exit(7)
"""


def _alive(pid: int) -> bool:
    # Independently inspect the recorded PID; do not reuse family membership
    # detection as the oracle for the mechanism being tested.
    reply = SubprocessProbe().run(
        ["/bin/ps", "-p", str(pid), "-o", "stat="], timeout_seconds=HANG_GUARD_S
    )
    assert reply is not None, "could not inspect the child process"
    state = reply.stdout.strip()
    return bool(state) and not state.startswith("Z")


def _assert_ended(pids: list[int]) -> None:
    threads = OsThreads()
    deadline = threads.now() + HANG_GUARD_S
    while alive := [pid for pid in pids if _alive(pid)]:
        assert threads.now() < deadline, f"command descendants survived: {alive}"
        threads.sleep(0.01)


def _verify_family_lifetime(tmp_path: Path, ending: str, *, confined: bool) -> None:
    profile = SeatbeltSandbox(
        workspace=tmp_path.resolve(), sandbox_exec_path="/usr/bin/sandbox-exec"
    )
    bystander = subprocess.Popen(["/bin/sleep", "600"])
    command = [
        sys.executable,
        "-c",
        _PROGRAM,
        "hold" if ending == "owner_killed" else ending,
        str(bystander.pid),
    ]
    if confined:
        command = profile.wrap(command)
    process = subprocess.Popen(  # noqa: S603  # lint-waiver: LW-731027 [S603]; this e2e wiring probe needs the relay PID and duplex pipes to kill only the relay after readiness. The process interfaces intentionally hide that PID, so using them would not test owner loss independently of group signalling. All arguments are constructed in this test.
        owned_command(command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    children: list[int] = []
    try:
        assert process.stdout is not None
        ready, _, _ = select.select([process.stdout], [], [], HANG_GUARD_S)
        assert ready, "child did not report readiness"
        children = json.loads(process.stdout.readline())
        if ending == "owner_killed":
            # Kill only the relay, not the keeper, group or the command. EOF
            # on the private lifeline must independently stop every child.
            process.kill()
        _out, err = process.communicate(timeout=HANG_GUARD_S)
        assert process.returncode == (-signal.SIGKILL if ending == "owner_killed" else 7), err
        _assert_ended(children)
        assert bystander.poll() is None
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=HANG_GUARD_S)
        for pid in children:
            if _alive(pid):
                os.kill(pid, signal.SIGKILL)
        bystander.kill()
        bystander.wait(timeout=HANG_GUARD_S)


@pytest.mark.parametrize("ending", ["complete", "owner_killed"])
def test_native_family_ends_detached_children(tmp_path: Path, ending: str) -> None:
    _verify_family_lifetime(tmp_path, ending, confined=False)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires real Seatbelt")
@pytest.mark.parametrize("ending", ["complete", "owner_killed", "signal"])
def test_seatbelt_family_lifetime_and_descendant_signalling(tmp_path: Path, ending: str) -> None:
    _verify_family_lifetime(tmp_path, ending, confined=True)


def test_local_runner_ends_a_background_sleep_at_command_completion(tmp_path: Path) -> None:
    runner = LocalShellRunner(
        tmp_path, threads=OsThreads(), launcher=SubprocessPipedProcessLauncher()
    )
    pid = 0
    try:
        result = runner.execute(
            "sleep 600 </dev/null >/dev/null 2>&1 & echo $!", timeout=10, output=DISCARD
        )
        assert result.exit_code == 0
        pid = int(result.stdout.strip())
        _assert_ended([pid])
    finally:
        if pid and _alive(pid):
            os.kill(pid, signal.SIGKILL)
