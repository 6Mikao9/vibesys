"""Rejected server launches release process-local ephemeral socket resources.

Every rejected argument list runs through the public ``main`` in a forked child of the test
(``run_in_child``: the child points the temp root at a private directory, which is
process-wide state). One case runs the real interpreter to prove the wiring.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from functools import partial
from pathlib import Path
from string import ascii_lowercase

from hypothesis import example, given, settings
from hypothesis.strategies import SearchStrategy, integers, just, one_of, text

from entrypoints.server import main
from vs_sim.api.testing import examples, run_in_child

_LOWER = text(alphabet=ascii_lowercase, min_size=1, max_size=8)
_REJECTED_WEB_FLAGS: SearchStrategy[list[str]] = one_of(
    one_of(
        integers(min_value=65_536, max_value=1 << 31),
        integers(max_value=-1),
        _LOWER,
    ).map(lambda port: ["--web-port", str(port)]),
    # An origin is scheme, host and port: a path, or no scheme at all, is rejected.
    _LOWER.map(lambda path: ["--web-origin", f"http://localhost:5173/{path}"]),
    _LOWER.map(lambda host: ["--web-origin", host]),
    just(["--web-origin"]),
)


def _rejected_launch(arguments: list[str], *, web: bool) -> tuple[object, list[str]]:
    """Call ``main`` and list its temp root while the failure is still live.

    Call through ``run_in_child``: it redirects the process-wide temp root.
    """
    with tempfile.TemporaryDirectory() as base:
        temp_root = Path(base) / "temp"
        temp_root.mkdir()
        tempfile.tempdir = str(temp_root)
        invocation = (
            ["--web", "--web-instance", str(Path(base) / "instance.json"), *arguments]
            if web
            else arguments
        )
        try:
            main(invocation)
        except SystemExit as error:
            code = error.code
        else:
            message = "server entrypoint accepted rejected arguments"
            raise AssertionError(message)
        return code, sorted(path.name for path in temp_root.iterdir())


@example(arguments=["--web-port", "99999"])
@example(arguments=["--web-origin", "http://localhost:5173/app"])
@example(arguments=["--web-origin"])
@settings(max_examples=examples(pr=6, nightly=40), deadline=None)
@given(arguments=_REJECTED_WEB_FLAGS)
def test_a_rejected_web_flag_creates_no_ephemeral_socket_directory(
    arguments: list[str],
) -> None:
    assert run_in_child(partial(_rejected_launch, arguments, web=True)) == (2, [])


def test_a_missing_control_socket_creates_no_ephemeral_web_socket_directory() -> None:
    assert run_in_child(partial(_rejected_launch, [], web=False)) == (2, [])


def test_the_real_server_process_rejects_a_launch_without_leaving_a_socket_directory(
    tmp_path: Path,
) -> None:
    """The one subprocess case: the real interpreter, entrypoint wiring included."""
    temp_root = tmp_path / "temp"
    temp_root.mkdir()
    probe = """
import json
import sys
from pathlib import Path

from entrypoints.server import main

failure = None
try:
    main(["--web", "--web-instance", sys.argv[1], "--web-port", "99999"])
except SystemExit as error:
    failure = error
if failure is None:
    raise AssertionError("server entrypoint accepted rejected arguments")
print(json.dumps({"exit_code": failure.code, "entries": sorted(path.name for path in Path(sys.argv[2]).iterdir())}))
"""
    completed = subprocess.run(  # noqa: S603  # lint-waiver: LW-101110 [S603]; execute the current interpreter with a fixed inline probe and fixed arguments in the real-API e2e tier
        [sys.executable, "-c", probe, str(tmp_path / "instance.json"), str(temp_root)],
        env={**os.environ, "TMPDIR": str(temp_root)},
        capture_output=True,
        text=True,
        check=True,
    )

    assert json.loads(completed.stdout) == {"exit_code": 2, "entries": []}
