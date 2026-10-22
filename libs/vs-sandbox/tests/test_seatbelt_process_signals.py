"""A confined command can stop its descendants without signalling the operator."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from vs_sandbox.api import SeatbeltSandbox

if TYPE_CHECKING:
    from pathlib import Path


class _SignalPolicy:
    """Interpret the profile's signal allow rules, with all other targets denied."""

    def __init__(self, profile: str) -> None:
        self._targets = re.findall(r"\(allow signal(?: \(target ([a-z-]+)\))?\)", profile)

    def permits(self, *, same_process: bool, same_policy: bool) -> bool:
        return any(
            not target
            or (target == "self" and same_process)
            or (target == "same-sandbox" and same_policy)
            for target in self._targets
        )


@pytest.mark.regression
@pytest.mark.parametrize(
    ("same_process", "same_policy", "allowed"),
    [(True, True, True), (False, True, True), (False, False, False)],
    ids=["self", "descendant", "operator"],
)
def test_seatbelt_signal_policy_preserves_descendant_control_and_operator_boundary(
    tmp_path: Path, *, same_process: bool, same_policy: bool, allowed: bool
) -> None:
    profile = SeatbeltSandbox(
        workspace=tmp_path, sandbox_exec_path="/usr/bin/sandbox-exec"
    ).profile()

    assert (
        _SignalPolicy(profile).permits(same_process=same_process, same_policy=same_policy)
        is allowed
    )
