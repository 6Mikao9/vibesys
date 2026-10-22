"""Resume preflight proves the payload it decoded still has the saved identity."""

from __future__ import annotations

import json
from hashlib import sha256
from typing import TYPE_CHECKING, ClassVar, Literal

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

import vs_core.api as core
from vs_project.api import FakeStateStore, StoredEnvelope
from vs_runtime.api.core import (
    CoreResumeError,
    CoreRuntime,
    CoreRuntimeBindings,
    RuntimeRecord,
    resolve_core_resume,
)
from vs_runtime.api.core_shell_fixtures import CounterConfig

from .test_core_preflight import _commit, driver, project_run, record

if TYPE_CHECKING:
    from pathlib import Path

    from pydantic import BaseModel


class _Outcome(core.Value):
    done: bool = True


class _Prompt(core.Value):
    text: str


class _ExtendedPrompt(_Prompt):
    combinable: tuple[str, ...] = ()


class _Original(core.OperationRequest):
    kind: Literal["test.render"] = "test.render"
    lifecycle: Literal[core.LifecycleClass.IDEMPOTENT_WRITE] = core.LifecycleClass.IDEMPOTENT_WRITE
    outcome_model: ClassVar[type[BaseModel]] = _Outcome
    context: _Prompt


class _Extended(_Original):
    context: _ExtendedPrompt


def _registry(model: type[core.OperationRequest]) -> core.OperationRegistry:
    return core.OperationRegistry(
        (
            core.OperationRegistration(
                descriptor=core.OperationDescriptor(
                    kind="test.render",
                    request_schema=core.SchemaRef(name="test.render", version=1),
                    outcome_schema=core.SchemaRef(name="test.render.outcome", version=1),
                    lifecycle=core.LifecycleClass.IDEMPOTENT_WRITE,
                    inspect=True,
                ),
                request_model=model,
                outcome_model=_Outcome,
            ),
        )
    )


def _with_receipt(run_id: str, text: str, *, extended: bool) -> RuntimeRecord[core.FlowState]:
    saved = record(run_id)
    model = _Extended if extended else _Original
    context = _ExtendedPrompt(text=text) if extended else _Prompt(text=text)
    codec = _registry(model)
    decision = codec.validate_decision(
        core.Operation(
            decision_id=core.DecisionId(root="planner.render.1"),
            scope=core.Scope(owner=core.RunId(root=run_id), generation=0),
            request=model(context=context),
        )
    )
    # Independent canonical fingerprint; these fixtures contain no unordered collections.
    fingerprint = sha256(
        json.dumps(
            decision.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    receipt = core.DecisionReceipt(
        decision_id=decision.decision_id,
        decision=decision,
        feedback=core.Accepted(decision_id=decision.decision_id),
        payload_digest=fingerprint,
    )
    state = saved.envelope.core
    state = state.model_copy(update={"run": state.run.model_copy(update={"receipts": (receipt,)})})
    return RuntimeRecord[core.FlowState].fresh(saved.envelope.model_copy(update={"core": state}))


@pytest.mark.regression
@example(text="planner", extended=False)
@given(text=st.text(max_size=50), extended=st.booleans())
def test_added_default_is_rejected_before_resume_changes_the_run(
    tmp_path_factory: pytest.TempPathFactory, text: str, *, extended: bool
) -> None:
    """Matching code accepts its own receipts; added serialized defaults require migration."""
    project, run_id = project_run(tmp_path_factory.mktemp("receipt"))
    _commit(project, run_id, _with_receipt(run_id, text, extended=extended))
    before = project.state.portable_run_export(run_id)
    matching = _registry(_Extended if extended else _Original)
    assert resolve_core_resume(project, driver(), registry=matching).run_id == run_id
    if extended:
        assert (
            resolve_core_resume(project, driver(), registry=_registry(_Extended)).run_id == run_id
        )
    else:
        with pytest.raises(CoreResumeError) as failure:
            resolve_core_resume(project, driver(), registry=_registry(_Extended))
        diagnostic = failure.value.diagnostic
        assert diagnostic.code == "core_resume_invalid"
        assert "planner.render.1.digest" in diagnostic.message
        assert "version that wrote this decision" in diagnostic.message
        assert "migration" in diagnostic.message
    assert project.state.portable_run_export(run_id) == before


def test_resume_preflight_does_not_materialize_a_writer_lock(tmp_path: Path) -> None:
    project, run_id = project_run(tmp_path)
    _commit(project, run_id, _with_receipt(run_id, "planner", extended=False))
    (project.state.state_store_namespace(run_id).external_directory() / "store.lock").unlink()
    before = project.state.portable_run_export(run_id)
    assert resolve_core_resume(project, driver(), registry=_registry(_Original)).run_id == run_id
    assert project.state.portable_run_export(run_id) == before


@pytest.mark.regression
@example(text="planner", prepare=True)
@example(text="planner", prepare=False)
@given(text=st.text(max_size=50), prepare=st.booleans())
def test_direct_runtime_start_rejects_changed_receipts_before_acquiring_a_lease(
    text: str, *, prepare: bool
) -> None:
    saved = _with_receipt("run", text, extended=False)
    store = FakeStateStore()
    fence = store.acquire("writer", now=0, duration=1)
    assert fence is not None
    store.commit(
        None,
        StoredEnvelope(revision=0, schema_version=1, payload=saved.model_dump_json().encode()),
        fence,
        now=0,
    )
    assert store.release(fence, now=0)
    before = store.load()
    shell = CoreRuntime(
        store,
        driver(),
        saved.envelope.core,
        config=CounterConfig(),
        bindings=CoreRuntimeBindings(registry=_registry(_Extended)),
    )
    if prepare:
        with pytest.raises(CoreResumeError, match=r"planner.render.1.digest"):
            shell.prepare_start("reader")
    else:
        with pytest.raises(CoreResumeError, match=r"planner.render.1.digest"):
            shell.start("reader", now_at=1, lease_duration=10)
    assert store.load() == before
    assert store.acquire("next-writer", now=1, duration=1) is not None
