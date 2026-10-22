"""A result shows the host around the measurement, and a result without one does not."""

from __future__ import annotations

from vibesys.prompts import CORE_PROTOCOL_DIR
from vibesys.run.environment import agent_environment_notes
from vs_prompts.api import TemplateRenderer
from vs_runtime.api.infrastructure import AgentPaths, EvalHostTenancy, RunEnvironmentView

_EXCERPT = """\
{% from '_evaluations.j2' import trusted_evaluations %}
{{ trusted_evaluations(items) }}"""

_SENTENCE = (
    "The evaluation host is shared, so measurements may include contention from other work; "
    "decide how to handle it."
)


def _item(*, host_snapshot: object | None = None) -> dict[str, object]:
    item: dict[str, object] = {
        "evidence_id": "e1",
        "kind": "benchmark",
        "revision": {"revision_id": {"root": "abc123"}},
        "passed": True,
        "status": "settled",
        "metrics": [],
        "partial": None,
        "feedback": "",
    }
    if host_snapshot is not None:
        item["host_snapshot"] = host_snapshot
    return item


def _render(items: list[dict[str, object]]) -> str:
    return str(TemplateRenderer(CORE_PROTOCOL_DIR).render_string(_EXCERPT, items=items))


def test_the_shared_host_sentence_is_stated_only_when_the_host_is_shared() -> None:
    def notes(tenancy: EvalHostTenancy) -> str:
        view = RunEnvironmentView(
            paths=AgentPaths(),
            workspace_git_writable=True,
            prompt_notes="Commands run in a container.",
            evaluation_host_tenancy=tenancy,
        )
        return agent_environment_notes(view)

    assert _SENTENCE in notes(EvalHostTenancy.SHARED)
    assert _SENTENCE not in notes(EvalHostTenancy.EXCLUSIVE)


def test_a_benchmark_shows_the_host_around_it_and_a_bare_result_does_not() -> None:
    shown = _render(
        [
            _item(
                host_snapshot={
                    "start": {
                        "load_1": 50.0,
                        "load_5": 20.0,
                        "load_15": 10.0,
                        "cpu_count": 12,
                        "memory_free_bytes": 100,
                        "memory_pressure": "8%",
                        "gpu_processes": ["9 compile"],
                    },
                    "end": {
                        "load_1": 4.0,
                        "load_5": 4.0,
                        "load_15": 4.0,
                        "cpu_count": 12,
                        "memory_free_bytes": 200,
                        "memory_pressure": None,
                        "gpu_processes": [],
                    },
                }
            )
        ]
    )
    hidden = _render([_item()])

    assert "Host at start: load 50.0/20.0/10.0" in shown
    assert "gpu processes: 9 compile" in shown
    assert "no other gpu processes" in shown
    assert "Host at start" not in hidden
