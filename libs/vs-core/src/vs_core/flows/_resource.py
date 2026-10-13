"""``@resource``: acquire and always release, written like ``contextlib.asynccontextmanager``."""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Concatenate

from vs_core._registry import ContractError

from ._errors import Stopped

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from ._flow import Flow


class _Resource[T]:
    """The ``async with`` of one resource. Entry is a checkpoint; setup and teardown are shielded."""

    def __init__(self, ctx: Flow, body: AsyncGenerator[T]) -> None:
        self._ctx = ctx
        self._body = body

    async def __aenter__(self) -> T:
        try:
            self._ctx.checkpoint()  # a stopped task never acquires
        except Stopped:
            await self._body.aclose()
            raise
        with self._ctx.shielded():
            return await anext(self._body)

    async def __aexit__(
        self, kind: type[BaseException] | None, error: BaseException | None, _tb: object
    ) -> bool:
        with self._ctx.shielded():
            if error is None:
                return await self._finished()
            try:
                await self._body.athrow(error)
            except StopAsyncIteration:
                return True  # the resource handled the exit; the body's exception ends here
            except BaseException as raised:
                if raised is error:
                    return False
                raise
            raise ContractError(("resource",), "a resource yields exactly once")

    async def _finished(self) -> bool:
        try:
            await anext(self._body)
        except StopAsyncIteration:
            return False
        raise ContractError(("resource",), "a resource yields exactly once")


def resource[**P, T](
    body: Callable[Concatenate[Flow, P], AsyncGenerator[T]],
) -> Callable[Concatenate[Flow, P], _Resource[T]]:
    """Make ``async with name(ctx, ...) as value`` from an async generator that yields once.

    The code before the ``yield`` acquires, the code after releases, and an exception of the
    ``async with`` body (a ``Stopped``, say) is thrown in at the ``yield`` so the release can
    tell how the body ended. Both parts run shielded: a stop that arrives meanwhile is delivered
    at the first ``await`` after. Entering checks for a pending stop first, so a stopped task
    never acquires.
    """

    @wraps(body)
    def enter(ctx: Flow, *args: P.args, **kwargs: P.kwargs) -> _Resource[T]:
        return _Resource(ctx, body(ctx, *args, **kwargs))

    return enter
