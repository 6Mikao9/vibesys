"""A host that dies while its search holds a workspace leaves the search's code to nobody.

The search runs inside the host's event loop but is not driven by it. A loop that heard of the
search's async generators (a flow's ``@resource`` bodies) would close the ones still alive when
it shuts down by running their release as a loop task, which cannot run a ``ctx`` wait and
fails loudly. A dead host's search must start no more work: the next host settles the
workspace from the journal.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from tests.support.crash_harness import all_crossings, converges_after, following, name

if TYPE_CHECKING:
    import pytest

HOLDING = "durable_write:commit#27"
"""A crash while the skeleton's search holds a workspace, then one right after the restart."""


def test_a_dead_host_leaves_no_search_generator_to_the_event_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = next(c for c in all_crossings() if name(c) == HOLDING)
    with caplog.at_level(logging.ERROR, logger="asyncio"):
        for second in following(first, 1):
            converges_after(first, second)
    assert [r.getMessage() for r in caplog.records if r.name == "asyncio"] == []
