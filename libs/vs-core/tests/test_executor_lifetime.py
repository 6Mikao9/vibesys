"""Every request kind is classified once: does its executor outlive the host that dispatched it?

The classification is core's, from the request kind alone (`executor_lifetime`). An agent
session and its turns are owned by the host instance that started them, so they die with it;
files, git, jobs and receipts outlive it. The table below is the whole classification, and the
tests keep it total: a request kind added without a row fails here before it can ship.
"""

from __future__ import annotations

from typing import Annotated, cast, get_args, get_origin

import pytest

import vs_core.api as core

INSTANCE: set[type[core.RequestBase]] = {
    core.EnsureSession,
    core.DispatchTurn,
    core.ResumeSessionTurn,
    core.InspectTurn,
    core.CancelTurn,
    core.CloseSession,
}
"""The agent session requests: the executor is the host instance."""

DURABLE: set[type[core.RequestBase]] = {
    core.EnsureWorkspace,
    core.RestoreRevision,
    core.SnapshotAndRetain,
    core.SnapshotAndRetainRun,
    core.RetainRevision,
    core.DiscardWorkspace,
    core.AdoptRevision,
    core.VerifyAdoption,
    core.SubmitMeasurement,
    core.ObserveOwnedJob,
    core.InspectOwnedJob,
    core.CancelOwnedJob,
    core.CollectEvidence,
    core.CloseAttemptScope,
    core.InspectRequest,
    core.CancelOwnedResource,
    core.BlockRequest,
}
"""Workspaces, git, adoption, jobs, evidence and the reconciliation commands: what they did lives
in files, git, a remote scheduler or a durable receipt."""


def _classes(declared: object) -> set[type]:
    """The concrete classes a (possibly aliased, annotated, nested) union names."""
    if isinstance(declared, type):
        return {declared}
    if hasattr(declared, "__value__"):
        return _classes(declared.__value__)
    if get_origin(declared) is Annotated:
        return _classes(get_args(declared)[0])
    return {kind for member in get_args(declared) for kind in _classes(member)}


def _unvalidated(kind: type[core.RequestBase]) -> core.Request:
    """An instance of the request class, built without its fields: its kind is all that is read."""
    return cast("core.Request", kind.model_construct())


def _kinds() -> set[type]:
    """Every concrete request class of the `Request` union, found from the union itself."""
    return _classes(core.Request)


def _registered(lifecycle: core.LifecycleClass) -> core.ExecuteRegisteredOperation:
    """A registered operation of the given lifecycle class, built without the codec."""
    schema = core.OperationSchemaRef.model_construct(lifecycle=lifecycle)
    operation = core.OperationWire.model_construct(schema_ref=schema)
    return core.ExecuteRegisteredOperation.model_construct(operation=operation)


def test_the_table_covers_every_request_kind_once() -> None:
    kinds = _kinds() - {core.ExecuteRegisteredOperation}
    assert kinds == INSTANCE | DURABLE
    assert not INSTANCE & DURABLE


@pytest.mark.parametrize("kind", sorted(INSTANCE, key=lambda value: value.__name__))
def test_an_agent_session_request_has_the_host_instance_as_its_executor(
    kind: type[core.RequestBase],
) -> None:
    assert core.executor_lifetime(_unvalidated(kind)) is core.ExecutorLifetime.INSTANCE


@pytest.mark.parametrize("kind", sorted(DURABLE, key=lambda value: value.__name__))
def test_every_other_request_has_a_durable_executor(kind: type[core.RequestBase]) -> None:
    assert core.executor_lifetime(_unvalidated(kind)) is core.ExecutorLifetime.DURABLE


@pytest.mark.parametrize("lifecycle", list(core.LifecycleClass))
def test_a_registered_operation_is_instance_bound_only_when_it_is_an_agent_turn(
    lifecycle: core.LifecycleClass,
) -> None:
    expected = (
        core.ExecutorLifetime.INSTANCE
        if lifecycle == core.LifecycleClass.SESSION_TURN
        else core.ExecutorLifetime.DURABLE
    )
    assert core.executor_lifetime(_registered(lifecycle)) is expected
