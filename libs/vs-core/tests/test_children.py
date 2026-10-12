"""``Children`` stamps decisions with the child's key and unstamps the results it routes back.

Properties over the public API: a child sees its own ids, never its siblings' or its
parent's stamp; a broadcast reaches every child in key order; an event for a finished child
is dropped, logged and never delivered.
"""

from hypothesis import given
from hypothesis import strategies as st

import vs_core.api as core

SCOPE = core.Scope(owner=core.initial_state().run.run_id, generation=0)
KEYS = st.lists(
    st.text(alphabet="abcdefgh", min_size=1, max_size=4), min_size=1, max_size=5, unique=True
)
IDS = st.text(alphabet="wxyz0123", min_size=1, max_size=4)


class Seen(core.Value):
    """A child machine that remembers the decision ids of the events it was given."""

    decisions: tuple[str, ...] = ()
    stops: int = 0


def _wait(identity: str) -> core.Wait:
    work = core.Withdraw(
        decision_id=core.DecisionId(root=identity),
        scope=SCOPE,
        target=core.AttemptRef(attempt_id=core.AttemptId(root="a"), generation=0),
        disposition=core.Park(),
    )
    return core.Wait(request=work, until=core.Within(limit=5.0))


def _update(machine: Seen, event: core.Event) -> tuple[Seen, list[core.Decision]]:
    if isinstance(event, core.Failed):
        assert event.decision is not None
        seen = Seen(decisions=(*machine.decisions, event.decision.root), stops=machine.stops)
        return seen, [_wait("next")]
    return Seen(decisions=machine.decisions, stops=machine.stops + 1), []


def _failed(identity: str) -> core.Failed:
    return core.Failed(
        reason=core.FailureReason.TIMED_OUT,
        decision=core.DecisionId(root=identity),
        limit=5.0,
    )


def _children(keys: list[str]) -> core.Children[str, Seen]:
    children: core.Children[str, Seen] = core.Children()
    for key in keys:
        children = children.put(key, Seen())
    return children


def _ids(decisions: list[core.Decision]) -> list[str]:
    return [d.request.decision_id.root for d in decisions if isinstance(d, core.Wait)]


@given(keys=KEYS, identity=IDS, data=st.data())
def test_a_result_reaches_the_child_that_made_the_decision_unstamped(
    keys: list[str], identity: str, data: st.DataObject
) -> None:
    key = data.draw(st.sampled_from(keys))
    children = _children(keys)
    after, decisions = children.route_event(_failed(f"{key}/{identity}"), _update)
    assert after.get(key).decisions == (identity,)
    assert all(after.get(other) == Seen() for other in keys if other != key)
    assert _ids(decisions) == [f"{key}/next"]
    assert after.dropped == ()


@given(keys=KEYS)
def test_a_broadcast_reaches_every_child_once_in_key_order(keys: list[str]) -> None:
    children = _children(keys)
    stop = core.StopRequested(
        control=core.ControlInput(control_id=core.ControlId(root="c"), action="stop")
    )
    after, decisions = children.broadcast(stop, _update)
    assert [after.get(key).stops for key in keys] == [1] * len(keys)
    assert decisions == []
    assert after.keys() == tuple(sorted(keys))


@given(keys=KEYS, identity=IDS)
def test_an_event_for_a_finished_child_is_dropped_and_logged(
    keys: list[str], identity: str
) -> None:
    gone = keys[0]
    children = _children(keys).remove(gone)
    event = _failed(f"{gone}/{identity}")
    after, decisions = children.route_event(event, _update)
    assert decisions == []
    assert after.members == children.members
    assert after.dropped == (
        core.Dropped(
            event=core.EventKind.FAILED, decision=core.DecisionId(root=f"{gone}/{identity}")
        ),
    )


@given(
    outer=st.text(alphabet="ab", min_size=1, max_size=3),
    inner=st.text(alphabet="cd", min_size=1, max_size=3),
    identity=IDS,
)
def test_nested_children_stamp_inside_out_and_unstamp_outside_in(
    outer: str, inner: str, identity: str
) -> None:
    leaves = core.Children[str, Seen]().put(inner, Seen())
    parents = core.Children[str, core.Children[str, Seen]]().put(outer, leaves)

    def through_inner(
        machine: core.Children[str, Seen], event: core.Event
    ) -> tuple[core.Children[str, Seen], list[core.Decision]]:
        return machine.route_event(event, _update)

    event = _failed(f"{outer}/{inner}/{identity}")
    after, decisions = parents.route_event(event, through_inner)
    assert after.get(outer).get(inner).decisions == (identity,)
    assert _ids(decisions) == [f"{outer}/{inner}/next"]
