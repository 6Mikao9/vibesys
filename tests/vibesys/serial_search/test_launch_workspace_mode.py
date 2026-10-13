"""Where the rounds work follows the run environment, and the operator cannot choose it."""

from __future__ import annotations

import pytest

from vibesys.orchestration.multi.launch import MultiLaunch
from vibesys.orchestration.single.launch import SingleLaunch
from vs_core.api import WorkspaceMode

LAUNCHES = pytest.mark.parametrize("launch", [SingleLaunch, MultiLaunch])
OPTIONS = {"max_rounds": 1, "interface": "inprocess"}


@LAUNCHES
@pytest.mark.parametrize("mode", list(WorkspaceMode))
def test_the_search_works_in_the_root_exactly_when_the_host_has_no_candidate_sandboxes(
    launch: type[SingleLaunch] | type[MultiLaunch], mode: WorkspaceMode
) -> None:
    options = launch.model_validate(OPTIONS)
    config = (
        options.search_config(workspace_mode=mode)
        if launch is SingleLaunch
        else options.search_config(profiling=False, workspace_mode=mode)  # ty: ignore[unknown-argument]
    )

    assert config.exclusive_root is (mode is WorkspaceMode.EXCLUSIVE_ROOT)


@LAUNCHES
def test_the_operator_cannot_choose_where_the_rounds_work(
    launch: type[SingleLaunch] | type[MultiLaunch],
) -> None:
    with pytest.raises(ValueError, match="exclusive_root"):
        launch.model_validate({**OPTIONS, "exclusive_root": True})
