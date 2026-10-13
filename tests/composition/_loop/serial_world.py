"""The serial searches' world on the production shell: what their agents reply and measure.

`SerialWorld` is a vs-dst `World`: a serial search (single or multi), its config and the scripted
far side of its run. A test builds one from plain data, simulates it and asserts on the run::

    world = SerialWorld.single(max_rounds=2, throughputs=(120.0,))
    run = simulate(world)
"""

from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING

from tests.support.checkpoint_faults import FaultyCheckpoints
from tests.support.serial_script import TERMS, SerialExecutors, harness_of
from tests.vibesys.serial_search._replies import plan_reply

from vibesys.orchestration.multi.core_policy.api import requirements_for as multi_requirements
from vibesys.orchestration.multi.search.api import MultiConfig, multi
from vibesys.orchestration.multi.search.api import budget as multi_budget
from vibesys.orchestration.single.core_policy.api import requirements_for as single_requirements
from vibesys.orchestration.single.search.api import SingleConfig, single
from vibesys.orchestration.single.search.api import budget as single_budget
from vs_dst.api import RunSetup
from vs_runtime.api.testing import Launch

if TYPE_CHECKING:
    from collections.abc import Sequence

    from vs_core.api import CoreState, Request
    from vs_core.testing.drive import Answer


def single_implemented(*, passed: bool = True) -> str:
    """The single implementer's combined reply."""
    return json.dumps(
        {
            "summary": "did it",
            "expected_behavior": "faster",
            "self_review": "ok",
            "feedback": "" if passed else "fix it",
            "verdict": "pass" if passed else "fail",
            "bottlenecks": "",
            "suggestions": "",
            "profile_analysis": "",
        }
    )


def multi_implemented(outcome: str = "nominated") -> str:
    """The multi implementer's reply."""
    return json.dumps(
        {"summary": "did it", "expected_behavior": "faster", "hypothesis_outcome": outcome}
    )


def multi_reviewed(*, passed: bool = True) -> str:
    """The multi judge's verdict."""
    return json.dumps(
        {
            "analysis": "checked",
            "feedback": "" if passed else "fix it",
            "verdict": "pass" if passed else "fail",
        }
    )


class SerialWorld:
    """The designer, implementer, judge and evaluators of one single or multi run.

    A reply is handed out once, in the order requests are authorized, so a copy of the world
    taken mid-run continues from where the run stood.
    """

    def __init__(  # noqa: PLR0913  # LW-641207 [PLR0913]; the script of a run is independent lists of replies, one per role.
        self,
        search: str,
        *,
        designer: Sequence[str | Answer] | None = None,
        implementer: Sequence[str | Answer] | None = None,
        judge: Sequence[str | Answer] | None = None,
        throughputs: Sequence[float | None] = (100.0,),
        checkpoints: Sequence[str] = (),
        **settings: object,
    ) -> None:
        """Script a run of ``search`` (``single`` or ``multi``).

        ``designer``, ``implementer`` and ``judge`` are the replies of the n-th turn of that role:
        an agent's JSON, or an answer for a turn that ends another way. ``throughputs`` is what
        candidates measure (None: the benchmark fails), picked by a candidate's commit.
        ``checkpoints`` answers the n-th checkpoint request ``succeeded``, ``failed`` or
        ``retryable`` (cycling). Any other option is a config field of the search.
        """
        self.search = search
        if search == "single":
            selected = SingleConfig.model_validate({"max_rounds": 1, **settings})
            definition, budget, requirements = single, single_budget(selected), single_requirements
            implemented, judged = single_implemented, None
        else:
            selected = MultiConfig.model_validate({"max_rounds": 1, **settings})
            definition, budget, requirements = multi, multi_budget(selected), multi_requirements
            implemented, judged = multi_implemented, multi_reviewed
        self.config = selected
        self.setup = RunSetup(
            new_launch=lambda: Launch(definition, selected),
            harness=harness_of(definition, budget, requirements(TERMS)),
            schemas=definition.reply_schemas,
        )
        assert definition.operations is not None
        registry = definition.operations.registry
        self._executors = SerialExecutors(
            roles={
                "orchestrator": deque([plan_reply("h1")] if designer is None else designer),
                "implementer": deque(
                    [implemented()] * selected.max_rounds if implementer is None else implementer
                ),
                "judge": deque(
                    [] if judged is None else [judged()] * 8 if judge is None else judge
                ),
            },
            registry=registry,
            prefix=f"{search}-",
            throughputs=tuple(throughputs),
            # A turn past the script gets a reply that is no valid JSON, never silence: a silent
            # turn is lost only after its time limit, and a host that dies meanwhile leaves a
            # recovery check core cannot end (the gap named in `vs_core/_waits.py`).
            exhausted="not json",
        )
        self._script = FaultyCheckpoints(self._executors, tuple(checkpoints))

    @classmethod
    def single(cls, **script: object) -> SerialWorld:
        """A single run."""
        return cls("single", **script)  # ty: ignore[invalid-argument-type]

    @classmethod
    def multi(cls, **script: object) -> SerialWorld:
        """A multi run."""
        return cls("multi", **script)  # ty: ignore[invalid-argument-type]

    def headline(self, commit: str) -> float | None:
        """What the candidate of ``commit`` measures (a pure function of its commit)."""
        return self._executors.headline(commit)

    def __call__(self, request: Request, core: CoreState) -> Answer:
        """What the executors report for ``request``."""
        return self._script(request, core)
