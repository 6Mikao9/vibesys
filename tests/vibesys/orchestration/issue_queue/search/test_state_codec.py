"""The search state persists through the core envelope codec unchanged."""

from __future__ import annotations

import pytest
from pydantic import ValidationError
from tests.vibesys.orchestration.issue_queue.search.world import issue_queue_config

from vibesys.orchestration.issue_queue.core_policy.api import (
    build_search,
    plain_operation_registry,
)
from vibesys.orchestration.issue_queue.search.api import (
    STATE_SCHEMA,
    Bootstrapping,
    IssueQueueState,
    Reading,
    Tip,
)


def test_the_initial_state_round_trips_its_persisted_form() -> None:
    state = IssueQueueState()
    assert IssueQueueState.model_validate_json(state.model_dump_json()) == state


def test_a_populated_state_round_trips_its_persisted_form() -> None:
    state = IssueQueueState(
        run=Bootstrapping(),
        reads=3,
        bootstrapped=True,
        round_idx=1,
        processed=2,
        jobs_started=4,
        tip=Tip(revision=None, settlement=None),
    )
    assert IssueQueueState.model_validate_json(state.model_dump_json()) == state


def test_the_state_schema_version_matches_the_declaration() -> None:
    assert IssueQueueState().schema_version == STATE_SCHEMA.version
    assert build_search(issue_queue_config()).declaration.state_schema == STATE_SCHEMA


def test_the_state_a_run_starts_in_reads_the_board_first() -> None:
    assert isinstance(IssueQueueState().run, Reading)


def test_the_registry_declares_the_render_verify_interpret_and_board_operations() -> None:
    kinds = {item.kind for item in plain_operation_registry().descriptors}
    assert kinds == {
        "plain.render_role_artifacts",
        "plain.verify_parent_revision",
        "plain.interpret_evidence",
        "issue_board.read",
        "issue_board.apply_changes",
        "issue_board.log_progress",
    }


_V1_STATE = {
    "schema_version": 1,
    "cursor": "start",
    "board": {"issues": [], "fresh": True},
    "job": None,
    "ending": None,
}


def test_state_saved_by_the_cursor_based_strategy_is_refused_naming_its_version() -> None:
    with pytest.raises(ValidationError, match="schema_version"):
        IssueQueueState.model_validate(_V1_STATE)


def test_state_with_an_unknown_key_is_refused_naming_the_key() -> None:
    with pytest.raises(ValidationError, match="surprise"):
        IssueQueueState.model_validate({"schema_version": 2, "surprise": 1})
