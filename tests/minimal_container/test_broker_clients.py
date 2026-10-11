"""The broker clients run in the editor container against a real host broker.

``vibesys-gpu`` is the single-file client the agent runs for its own GPU commands. The
broker is the run's own, serving the editor container over a Unix socket mounted into
it; its ``srun`` is a program that runs the job locally.
"""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from tests.minimal_container.editor import Editor

pytestmark = pytest.mark.minimal_container


def test_the_gpu_client_runs_a_command_as_a_job(editor: Editor) -> None:
    status, output = editor.run(f"{shlex.quote(editor.gpu_client)} -- echo from-a-job")

    assert status == 0, output
    assert "from-a-job" in output
