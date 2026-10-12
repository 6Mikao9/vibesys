"""A wait the operator's stop must not abandon.

An operator stop interrupts a workstream wherever it is awaiting, and the wait it was in is
dropped. Two waits must not be dropped: opening the attempt (its generation is needed to settle
it) and closing it (the settlement is the result). They run as a child that is not stoppable,
and the stopped workstream only waits for that child.
"""

from pydantic import BaseModel

from vs_core.api.search import Event, Flow, Stopped, Within, Work


async def uninterrupted(
    ctx: Flow, key: str, request: Work, until: Within, *, reply: type[BaseModel] = BaseModel
) -> Event:
    """Wait for ``request`` to be answered, whether or not the operator stops the run meanwhile."""
    child = ctx.spawn(key, _wait, request, until, reply)
    try:
        return await ctx.join(child)
    except Stopped:
        return await ctx.join(child)


async def _wait(ctx: Flow, request: Work, until: Within, reply: type[BaseModel]) -> Event:
    return await ctx.wait(request, until, reply=reply)
