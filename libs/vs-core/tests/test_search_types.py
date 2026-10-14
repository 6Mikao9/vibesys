"""Type-level contract of the search API: what a search cannot write.

``ty`` is the test. Each line marked ``ty: ignore`` must be a type error; if the API
loosens so that the line type-checks, the ignore becomes unused and ``ty`` reports it.
CI enforces that with ``[tool.ty.rules] unused-ignore-comment = "error"`` in
``pyproject.toml``. Each test also checks that code which skips the type checker is still
refused at runtime.
"""

import pytest

import vs_core.api as core

DECISION = core.DecisionId(root="d")
SCOPE = core.Scope(owner=core.initial_state().run.run_id, generation=0)


def _work() -> core.Work:
    return core.Withdraw(
        decision_id=DECISION,
        scope=SCOPE,
        target=core.AttemptRef(attempt_id=core.AttemptId(root="a"), generation=0),
        disposition=core.Park(),
    )


def test_a_bare_request_is_not_a_decision() -> None:
    decisions: list[core.Decision] = [
        core.Wait(request=_work(), until=core.Within(limit=1.0)),
        core.WakeAt(time=1.0),
    ]
    # A bare request is work, not a decision: it only starts inside a Wait.
    decisions.append(_work())  # ty: ignore[invalid-argument-type]
    assert [type(d) for d in decisions[:2]] == [core.Wait, core.WakeAt]
    # Without the type checker, core still refuses the bare request, naming where it is.
    with pytest.raises(core.ContractError, match="start work inside a Wait") as refused:
        core.check_decisions(decisions)
    assert refused.value.path == ("decisions", 2)


def test_a_watch_alone_is_not_an_until() -> None:
    watch = core.WhileOpen(
        workspace=core.AttemptRef(attempt_id=core.AttemptId(root="a"), generation=0)
    )
    with pytest.raises(ValueError, match="until"):
        core.Wait(request=_work(), until=watch)  # ty: ignore[invalid-argument-type]


def test_a_wait_without_until_is_refused() -> None:
    with pytest.raises(ValueError, match="until"):
        core.Wait(request=_work())  # ty: ignore[missing-argument]
