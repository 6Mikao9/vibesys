"""A search declares the work it uses and the event kinds it handles; core checks they match."""

import pytest
from hypothesis import given
from hypothesis import strategies as st

import vs_core.api as core

W, E = core.WorkKind, core.EventKind

# The contract: the result event each kind of work ends with.
PIPELINE = {
    E.TURN_ENDED,
    E.TURN_YIELDED,
    E.YIELD_FAILED,
    E.RESUME_READY,
    E.CHECKPOINT_KNOWN,
}
RESULT = {
    W.ASK_AGENT: {E.TURN_FINISHED},
    W.REQUEST_TURN: PIPELINE,
    W.START_ATTEMPT: {E.WORKSPACE_OPENED},
    W.WITHDRAW: {E.WORKSPACE_CLOSED},
    W.MEASURE: {E.MEASUREMENT_FINISHED},
    W.PROPOSE_WINNER: {E.ADOPTION_FINISHED},
    W.OPERATION: {E.OPERATION_FINISHED},
    W.WAKE_AT: {E.TIME_REACHED},
}
ALWAYS = {E.FAILED, E.STOP_REQUESTED}
OPT_IN = {E.MESSAGE_DELIVERED, E.MESSAGE_DROPPED, E.INTERRUPTED} | PIPELINE
USES = st.frozensets(st.sampled_from(list(W)))


def _declaration(
    uses: frozenset[core.WorkKind], handles: set[core.EventKind]
) -> core.SearchDeclaration:
    base = core.initial_state().run.declaration
    return base.model_copy(update={"uses": uses, "handles": frozenset(handles)})


def _needed(uses: frozenset[core.WorkKind]) -> set[core.EventKind]:
    return ALWAYS | set().union(*(RESULT[kind] for kind in uses))


@given(uses=USES, opted=st.sets(st.sampled_from(sorted(OPT_IN))))
def test_a_declaration_that_handles_exactly_the_results_of_its_work_is_accepted(
    uses: frozenset[core.WorkKind], opted: set[core.EventKind]
) -> None:
    core.validate_search_declaration(_declaration(uses, _needed(uses) | opted))


@given(uses=USES.filter(bool), data=st.data())
def test_a_result_event_the_search_does_not_handle_is_refused_by_name(
    uses: frozenset[core.WorkKind], data: st.DataObject
) -> None:
    missing = data.draw(st.sampled_from(sorted(_needed(uses))))
    with pytest.raises(core.ContractError) as raised:
        core.validate_search_declaration(_declaration(uses, _needed(uses) - {missing}))
    assert raised.value.path[-1] == missing.value


@given(uses=USES, data=st.data())
def test_a_declared_event_no_used_work_can_produce_is_refused_by_name(
    uses: frozenset[core.WorkKind], data: st.DataObject
) -> None:
    dead = [kind for kind in E if kind not in _needed(uses) | OPT_IN]
    if not dead:
        return
    extra = data.draw(st.sampled_from(dead))
    with pytest.raises(core.ContractError) as raised:
        core.validate_search_declaration(_declaration(uses, _needed(uses) | {extra}))
    assert raised.value.path[-1] == extra.value
