"""Agent prompts describe trusted evaluation only through the evaluation tool (#1649).

Over every outer loop and run environment: no prompt names ``vibesys-gate`` or ``--gate``;
``vibesys-gpu`` appears only where the agent's own GPU commands are on and only as
``-- COMMAND``; and a loop that offers trusted evaluation offers it as the evaluation tool.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from tests.vibesys.orchestration.dynamic.core_policy.test_environment_notes import (
    _FRESH_TURNS,
    _plan_variables,
    _render,
)
from tests.vibesys.orchestration.dynamic.core_policy.test_prompt_templates import _CONTEXTS

from vibesys.orchestration.dynamic.strategy.api import PromptTemplate
from vibesys.prompts import PROMPTS_DIR, render_template

if TYPE_CHECKING:
    from hypothesis.strategies import DataObject


_SRC = Path(__file__).resolve().parents[2] / "src" / "vibesys"
_TEMPLATES = sorted(_SRC.rglob("*.j2"))
_ENVIRONMENTS = PROMPTS_DIR / "environments"
# The limits the environment hands its notes template (``AgentGpuFacts`` in vs_runtime).
_GPU_FACTS = SimpleNamespace(max_gpus=4, max_time_minutes=90, read_only_paths=())
# What the agent may be told about vibesys-gpu: the command form, or the name as a subject.
_GPU_USE = re.compile(r"vibesys-gpu(`| --gpus N --time MINUTES -- COMMAND| -- COMMAND)")


def _notes(name: str) -> str:
    """The environment notes of the named run environment, as the agent's prompts carry them."""
    if name == "slurm":
        return render_template(
            "slurm/prompt_notes.j2",
            template_dir=_ENVIRONMENTS,
            service_command="",
            agent_gpu=None,
            read_only_paths=[],
        )
    if name == "slurm+gpu":
        return render_template(
            "slurm/prompt_notes.j2",
            template_dir=_ENVIRONMENTS,
            service_command="python serve.py",
            agent_gpu=_GPU_FACTS,
            read_only_paths=["/data"],
        )
    return render_template(f"{name}/prompt_notes.j2", template_dir=_ENVIRONMENTS, history_root=None)


_NOTE_ENVIRONMENTS = ("docker", "slurm", "slurm+gpu")


def _assert_evaluation_only(text: str, *, gpu: bool) -> None:
    assert "vibesys-gate" not in text
    assert "--gate" not in text
    uses = [match.start() for match in re.finditer("vibesys-gpu", text)]
    assert len(uses) == len(_GPU_USE.findall(text))
    assert bool(uses) is gpu
    assert ("only the evaluation tool scores the candidate" in text) is gpu


@pytest.mark.parametrize("path", _TEMPLATES, ids=lambda path: str(path.relative_to(_SRC)))
def test_no_template_of_any_loop_names_a_shell_gate(path: Path) -> None:
    source = path.read_text(encoding="utf-8")

    assert "vibesys-gate" not in source
    assert "_gate_client" not in source
    assert "--gate " not in source
    if "vibesys-gpu" in source:
        # Only the slurm environment's notes name the client, and only in its allowed forms.
        assert path == _ENVIRONMENTS / "slurm" / "prompt_notes.j2"
        assert len(re.findall("vibesys-gpu", source)) == len(_GPU_USE.findall(source))


@pytest.mark.parametrize("environment", _NOTE_ENVIRONMENTS)
def test_every_environments_notes_describe_evaluation_only_through_the_tool(
    environment: str,
) -> None:
    _assert_evaluation_only(_notes(environment), gpu=environment == "slurm+gpu")


@pytest.mark.parametrize("environment", _NOTE_ENVIRONMENTS)
@pytest.mark.parametrize("template", _FRESH_TURNS, ids=lambda item: item.value)
@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], max_examples=5)
@given(data=st.data())
def test_the_dynamic_loops_prompts_offer_the_evaluation_tool_in_every_environment(
    environment: str, template: PromptTemplate, data: DataObject, tmp_path: Path
) -> None:
    text = _render(
        template, data.draw(_CONTEXTS[template]), _plan_variables(tmp_path, _notes(environment))
    )

    _assert_evaluation_only(text, gpu=environment == "slurm+gpu")
    if template in (PromptTemplate.IMPLEMENT, PromptTemplate.REVIEW):
        assert "submit_evaluation" in text
        assert "validate_evaluation_wait" in text
