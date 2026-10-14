"""Type-level checks (``ty`` verifies the ``assert_type`` calls; they are no-ops at run time)."""

from __future__ import annotations

from typing import TYPE_CHECKING, assert_type

import vs_core.api as core

if TYPE_CHECKING:
    from collections.abc import Awaitable


async def _attempt(tries: core.Tries[str]) -> int | core.Again[str]:
    return core.Again(reason=str(tries.number)) if tries.number < 2 else tries.number


async def _blocked_by(reasons: tuple[str, ...]) -> int | core.Again[str]:
    return core.Again(reason="x") if reasons else 1


async def _two_kinds(tries: core.Tries[str]) -> int | str | core.Again[str]:
    return core.Again(reason=str(tries.number))


async def retry_result_is_value_or_gave_up(ctx: core.Flow, policy: core.RetryPolicy) -> None:
    got = await ctx.retry(_attempt, policy)
    assert_type(got, int | core.GaveUp[str])
    match got:
        case core.GaveUp(reasons=reasons):
            assert_type(reasons, tuple[str, ...])
        case int() as number:
            assert_type(number, int)


async def retry_of_a_closure(ctx: core.Flow, policy: core.RetryPolicy) -> None:
    async def attempt(tries: core.Tries[str]) -> int | core.Again[str]:
        return await _blocked_by(tries.reasons)

    got = await ctx.retry(attempt, policy)
    assert_type(got, int | core.GaveUp[str])


async def retry_keeps_a_union_value(ctx: core.Flow, policy: core.RetryPolicy) -> None:
    got = await ctx.retry(_two_kinds, policy)
    assert_type(got, int | str | core.GaveUp[str])


def _unused(_: Awaitable[int]) -> None: ...
