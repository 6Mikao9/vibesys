"""A fake core for the single search: the shared serial world plus its two agents."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from tests.vibesys.serial_search._host import SerialHost
from tests.vibesys.serial_search._replies import plan_reply

from vibesys.orchestration.single.models import SingleAgentRoundResponse
from vibesys.orchestration.single.search.api import SingleConfig, single
from vs_core.api import Declined, FailureReason, Retained, TurnFailureKind
from vs_core.api import CheckpointDecline

if TYPE_CHECKING:
    from vs_core.api.testing import Asked

PLANNER = "orchestrator"
IMPLEMENTER = "implementer"


def config(**overrides: object) -> SingleConfig:
    """A one-round config, overridden by the test."""
    return SingleConfig.model_validate({"max_rounds": 1, **overrides})


def implemented(*, passed: bool = True, **extra: object) -> SingleAgentRoundResponse:
    """A combined implement, self-review and profile reply."""
    return SingleAgentRoundResponse.model_validate_json(
        json.dumps(
            {
                "summary": "did it",
                "expected_behavior": "faster",
                "self_review": "ok",
                "feedback": "" if passed else "fix it",
                "verdict": "pass" if passed else "fail",
                "bottlenecks": "",
                "suggestions": "",
                "profile_analysis": "",
                **extra,
            }
        )
    )


@dataclass
class SingleHost(SerialHost):
    """The world around a single flow."""

    designs: int = 0

    @classmethod
    def start(cls, selected: SingleConfig, *, restarting: bool, **world: object) -> SingleHost:
        """Start the single search of ``selected`` under a host."""
        return cls.begin(single, selected, restarting=restarting, **world)  # ty: ignore[invalid-return-type]

    def answer_agent(self, asked: Asked, variant: int) -> None:
        """Answer a designer or implementer turn; ``variant`` chooses how it goes."""
        if asked.label == PLANNER:
            self._planner(asked, variant)
        else:
            self._implementer(asked, variant)

    def _planner(self, asked: Asked, variant: int) -> None:
        match variant % 5:
            case 0 | 1:
                self.designs += 1
                self.run.reply_raw(asked, plan_reply(f"h{self.designs}"))
            case 2:
                self.run.reply_raw(asked, "not json")
            case 3:
                self.run.fail(asked, FailureReason.TIMED_OUT)
            case _:
                self.run.lose(asked)

    def _implementer(self, asked: Asked, variant: int) -> None:
        match variant % 6:
            case 0 | 1:
                self.run.reply(
                    asked,
                    implemented(passed=variant % 6 == 0),
                    checkpoint=Retained(revision=self.retained()),
                )
            case 2:
                self.run.fail_turn(asked, TurnFailureKind.PROVIDER_FAILED)
            case 3:
                self.run.fail(asked, FailureReason.TIMED_OUT)
            case 4:
                self.run.reply_raw(asked, "not json")
            case _:
                declined = Declined(reason=next(iter(CheckpointDecline)))
                self.run.reply(asked, implemented(), checkpoint=declined)
