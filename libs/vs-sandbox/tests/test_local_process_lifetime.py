"""The public local runner selects family ownership on every exit path."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from vs_sandbox.api import LocalShellRunner
from vs_sim.api import DISCARD
from vs_sim.api.testing import FakeFamilyPipedProcessLauncher, SimThreads

if TYPE_CHECKING:
    from vs_sim.api import ProcessIdentity
    from vs_sim.api.testing import FakeProcessFamily


@pytest.mark.regression
@pytest.mark.parametrize("ending", ["complete", "timeout", "cancel"])
@given(detach=st.lists(st.booleans(), min_size=1, max_size=12))
@example(detach=[False, True, True])
def test_no_background_or_daemonized_process_survives_a_command(
    ending: str, detach: list[bool]
) -> None:
    threads = SimThreads()

    def case() -> None:
        def program(family: FakeProcessFamily, leader: ProcessIdentity) -> None:
            parent = leader
            for new_session in detach:
                child = family.spawn(parent)
                if new_session:
                    family.setsid(child)
                if parent != leader:
                    family.exit(parent)
                parent = child
            if ending == "complete":
                family.exit(leader, 0)

        launcher = FakeFamilyPipedProcessLauncher(threads, program)
        runner = LocalShellRunner(Path.cwd(), threads=threads, launcher=launcher)
        cancel = threads.event()
        if ending == "cancel":
            cancel.set()

        result = runner.execute("scripted-command", timeout=1, cancel=cancel, output=DISCARD)

        assert not launcher.family.members()
        assert result.exit_code == {"complete": 0, "timeout": 124, "cancel": 143}[ending]

    threads.run(case)
