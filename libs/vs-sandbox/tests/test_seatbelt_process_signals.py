"""A confined command can stop its descendants without signalling the operator."""

from pathlib import Path

from vs_sandbox.api import SeatbeltSandbox


def test_seatbelt_allows_signals_only_within_the_inherited_confinement(tmp_path: Path) -> None:
    profile = SeatbeltSandbox(
        workspace=tmp_path, sandbox_exec_path="/usr/bin/sandbox-exec"
    ).profile()

    assert "(allow signal (target same-sandbox))" in profile
    assert "(allow signal)" not in profile
