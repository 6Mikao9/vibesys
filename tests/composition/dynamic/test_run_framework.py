"""What the framework decides around a dynamic run, seen through the production host.

The search names its best candidate and the framework ends the run: it adopts that candidate
when the search returns, when the operator stops the run and when the deadline passes. These
scenarios run the whole composition (CLI request, agent.toml, Fake cluster, scripted agents)
for the parts of that story a search cannot show on its own: the operator's per-role model
reaching a session, a task with no accuracy check, and the deadline. The operator's stop is
in ``test_stop``; the launch refusal for profiling without a capture is the last test.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from tests.composition._loop.dynamic_world import DynamicWorld
from tests.composition.dynamic._harness import (
    _AGENT_CONFIG,
    PASS,
    CoreRecords,
    LoopInput,
    ScriptedAgents,
    edit_to,
    portfolio,
    run_request,
    simulated_clock,
    workstream,
)
from tests.support.dynamic_script import FACTS, TERMS

from vibesys.api import RunStatus
from vibesys.dynamic_roles import ORCHESTRATOR
from vibesys.events import RunFailureKind
from vibesys.orchestration.dynamic.core_policy.api import requirements_for
from vibesys.orchestration.dynamic.search.api import dynamic
from vs_core.api import RetainedCandidate
from vs_runtime.api.testing import drive_shell

if TYPE_CHECKING:
    from pathlib import Path

# The search's reason for refusing a run it cannot give the capture it was told to use.


def _dispatched_turns(records: CoreRecords) -> list[dict[str, Any]]:
    """The agent turns the run dispatched, as the core recorded them."""
    return [
        item["request"]["turn"]
        for item in records.core["records"]["records"]
        if item["request"]["kind"] == "dispatch_turn"
    ]


def test_a_role_model_from_agent_toml_reaches_only_that_roles_sessions(tmp_path: Path) -> None:
    loop_input = LoopInput.create(
        tmp_path,
        agent_config=_AGENT_CONFIG + '[agent.roles.dynamic-implementer]\nmodel = "builder-model"\n',
    )
    agents = (
        ScriptedAgents()
        .plan(portfolio(workstream("H1")))
        .implement("H1", edit_to(2, "H1"))
        .judge("H1", PASS)
    )

    run = run_request(
        loop_input.request(),
        agents,
        slurm_process=loop_input.connector,
        state_stores=loop_input.state_stores,
    )

    assert run.error is None
    assert agents.unscripted == []
    models = {
        turn["session"]["role_id"]["root"]: turn["session"]["model"]
        for turn in _dispatched_turns(CoreRecords(loop_input, run.run_id))
    }
    # The other roles name no model of their own: they inherit the run's.
    assert models == {
        "dynamic-orchestrator": None,
        "dynamic-implementer": "builder-model",
        "dynamic-judge": None,
    }


_FAILS_ABOVE_FIVE = """\
import pathlib
namespace = {}
exec(pathlib.Path("queue.py").read_text(), namespace)
if namespace["VALUE"] > 5:
    raise ValueError("queue depth is too deep")
"""


def test_a_task_that_measures_no_accuracy_adopts_what_its_benchmark_admits() -> None:
    gated = DynamicWorld()
    benchmark_only = TERMS.model_copy(
        update={
            "measured": ("benchmark",),
            "accuracy_stage": None,
            "accuracy_gated": (),
        }
    )
    facts = FACTS.model_copy(update={"task": benchmark_only})
    hosting = replace(
        gated.setup.harness, facts=facts, requirements=requirements_for(benchmark_only)
    )

    run = drive_shell(gated.setup.new_launch(), gated, hosting, dynamic.reply_schemas)

    result = run.core.run.result
    assert result is not None
    assert result.outcome == "success"
    assert result.reason.startswith("adopted")
    assert isinstance(result.selection, RetainedCandidate)
    # No candidate was ever measured for accuracy, so none carries an accuracy proof.
    assert not any(
        stage.stage_id == "accuracy"
        for item in run.core.records.records
        if item.request.kind == "submit_measurement"
        for stage in item.request.plan.stages
    )


def test_a_task_that_requires_accuracy_keeps_the_input_over_a_candidate_that_fails_it(
    tmp_path: Path,
) -> None:
    loop_input = LoopInput.create(tmp_path)
    (loop_input.root / ".vibesys/tasks/default/accuracy.py").write_text(
        _FAILS_ABOVE_FIVE, encoding="utf-8"
    )
    agents = (
        ScriptedAgents()
        .plan(portfolio(workstream("H1")))
        .implement("H1", edit_to(6, "H1"))
        .judge("H1", PASS)
    )

    run = run_request(
        loop_input.request(),
        agents,
        slurm_process=loop_input.connector,
        state_stores=loop_input.state_stores,
    )

    assert run.error is None
    assert (run.succeeded, run.status) == (True, RunStatus.COMPLETED)
    records = CoreRecords(loop_input, run.run_id)
    assert not records.hypothesis("H1").rounds[0].eligible
    assert records.selection is not None
    assert records.selection["kind"] == "trusted_baseline"
    assert (loop_input.root / "queue.py").read_text(encoding="utf-8") == "VALUE = 1\n"


def test_the_deadline_ends_a_run_that_has_kept_nothing_through_the_framework(
    tmp_path: Path,
) -> None:
    loop_input = LoopInput.create(
        tmp_path, agent_config=_AGENT_CONFIG + "[run]\nmax_run_seconds = 1\n"
    )
    agents = ScriptedAgents().plan(portfolio(workstream("H1")))

    run = run_request(
        loop_input.request(),
        agents,
        clock=simulated_clock(),
        slurm_process=loop_input.connector,
        state_stores=loop_input.state_stores,
    )

    assert run.error is None
    assert run.status is RunStatus.FAILED
    assert run.result is not None
    failure = run.result.failure
    assert failure is not None
    assert failure.kind is RunFailureKind.DEADLINE
    assert failure.reason.startswith("deadline")
    assert agents.prompts(ORCHESTRATOR.id) == []
    assert (failure.workstreams_started, failure.candidates_kept) == (0, 0)
