"""Scripted agent replies in the shapes the serial searches' roles are asked to produce."""

from __future__ import annotations

import json


def plan_reply(identifier: str = "h1", **extra: object) -> str:
    """A designer reply: one new hypothesis."""
    return json.dumps(
        {
            "hypothesis_id": identifier,
            "title": f"title {identifier}",
            "hypothesis": f"claim {identifier}",
            "task": f"task {identifier}",
            "pass_criteria": "faster",
            "reasoning": "because",
            **extra,
        }
    )


def attribution() -> str:
    """What an attribution command prints (profile result protocol v1)."""
    return json.dumps(
        {
            "version": 1,
            "cost_unit": "ms",
            "components": [
                {"name": "gemm", "cost": 8.0, "share": 0.8, "evidence": ["a"]},
                {"name": "io", "cost": 2.0, "share": 0.2, "evidence": []},
            ],
        }
    )
