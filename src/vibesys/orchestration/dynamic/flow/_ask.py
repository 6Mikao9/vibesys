"""Asking an agent a question inside an attempt."""

from pydantic import BaseModel

from vibesys.orchestration.dynamic.flow._outcomes import Attempt
from vibesys.orchestration.dynamic.search.api import Role
from vs_core.api.search import AskAgent, DecisionId, Event, Flow, RevisionRef, Value, Within


async def ask(  # noqa: PLR0913  # LW-220431 [PLR0913]; each argument is a distinct fact of the ask; bundling them in a throwaway record only renames the parameters.
    ctx: Flow,
    attempt: Attempt,
    role: Role,
    prompt: Value,
    revision: RevisionRef,
    seconds: float,
    reply: type[BaseModel],
) -> Event:
    """Ask ``role`` inside the attempt: its reply, or the typed failure that ended the turn."""
    request = AskAgent(
        decision_id=DecisionId(root=role.value),
        scope=attempt.scope,
        agent=role.value,
        input=prompt,
        revision=revision,
    )
    return await ctx.wait(request, Within(limit=seconds), reply=reply)
