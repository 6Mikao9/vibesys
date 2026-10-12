"""Core's agent pipeline: one ``AskAgent`` in, one ``TurnFinished`` or ``Failed`` out.

``AgentTurns`` wraps a search. The search asks an agent with ``Wait(AskAgent(...), Within(n))``;
the wrapper turns that into the work core needs and hides the plumbing:

    render the prompt -> run the turn -> validate the reply against the agent's model
    -> ask for a correction of an invalid reply -> ask again after a lost turn (after a backoff)
    -> suspend a turn that waits for evaluations and resume it when core authorizes
    -> hold a write turn's reply until core recorded its checkpoint -> deliver ONE result.

Each ask is a *flow* in the wrapper's own state, which is persisted with the search's in one
write. A flow is a phase (what it waits for) over the facts of the ask. The wrapper is a pure
state machine like the search it wraps.

How often core asks again on its own is the agent's ``Retry``; an agent with ``Retry.none()``
gets ``Failed(INVALID_REPLY)`` (or ``Failed(LOST)``) at once and asks again itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, Field, ValidationError

from ._registry import ContractError
from ._strategy_operations import RenderedArtifacts
from ._waitable import WaitableReply, WaitingForDependency
from .types.agents import (
    Checkpoint,
    Correction,
    Declined,
    ResumeFacts,
    Retained,
    Retry,
)
from .types.common import (
    THIS_RUN,
    AttemptId,
    ContinuationId,
    DecisionId,
    InvocationRef,
    ObservationStatus,
    RevisionRef,
    RunFacts,
    SchemaRef,
    Scope,
    Seconds,
    Value,
    WorkspaceMode,
    WorkspaceRef,
)
from .types.kinds import ALWAYS, PRODUCES, EventKind, WorkKind
from .types.search import (
    CheckpointKnown,
    Event,
    OperationFinished,
    ResumeReady,
    RunView,
    TimeReached,
    TurnEnded,
    TurnFinished,
    TurnYielded,
    YieldFailed,
)
from .types.sessions import Access, SessionSpec, TurnFailureKind, TurnResult, TurnSpec
from .types.strategy import (
    AskAgent,
    Decision,
    Operation,
    RequestTurn,
    SearchDeclaration,
    StrategyState,
    Wait,
    WakeAt,
)
from .types.until import (
    Failed,
    FailureReason,
    ReplyError,
    ReplyErrorCode,
    WaitRefusal,
    Within,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ._ids import IdScheme
    from ._strategy_operations import StrategyOperations
    from .types.attempts import AttemptView
    from .types.evaluation import ResumeAuthorized
    from .types.search import Search

_MIN_ASK_SUFFIX = 2
_SEPARATOR = "~"
_RENDER_LIMIT = 300.0


@dataclass(frozen=True)
class Agent:
    """One agent a search may ask: who it is, what it answers with and how core retries it."""

    role: str
    reply: type[BaseModel]
    """The wire model of the reply (a ``WaitableReply[R]`` for an agent that may wait)."""
    schema: SchemaRef
    access: Access
    workspace: WorkspaceMode | None
    """The mode of the agent's workspace revision, or None for an agent that works in the run."""
    charge: Literal["free", "paid"]
    retry: Retry = field(default_factory=Retry)
    waits: bool = False
    """Whether the agent may end a turn waiting for its evaluations (its reply is waitable)."""
    checkpoint: Callable[[BaseModel], bool] | None = None
    """Whether a reply must wait for core's checkpoint of the turn before it is delivered."""


@dataclass(frozen=True)
class AgentProtocol:
    """The prompt contexts of the exchanges core itself starts: corrections and resumes."""

    correction: Callable[[str, tuple[ReplyError, ...]], Value]
    resume: Callable[[ResumeFacts], Value]
    wait_unrecorded: Callable[[str], Value]


class Rendering(Value):
    """The prompt of the next turn is being rendered."""

    kind: Literal["rendering"] = "rendering"


class Asked(Value):
    """A provider turn is running."""

    kind: Literal["asked"] = "asked"


class BackingOff(Value):
    """A lost turn waits out its backoff before it is asked again."""

    kind: Literal["backing_off"] = "backing_off"
    until: Seconds


class Holding(Value):
    """A valid reply waits for core to record or decline the checkpoint of its turn."""

    kind: Literal["holding"] = "holding"
    output: str


class Yielding(Value):
    """The turn ended with a wait; core has not yet recorded it."""

    kind: Literal["yielding"] = "yielding"


class Suspended(Value):
    """The turn is suspended until core authorizes its resume."""

    kind: Literal["suspended"] = "suspended"
    continuation: ContinuationId


type FlowPhase = Annotated[
    Rendering | Asked | BackingOff | Holding | Yielding | Suspended, Field(discriminator="kind")
]
type Charge = Literal["free", "paid", "correction", "resume"]


class Flow[I: Value](Value):
    """One ask: the facts that stay the same and the phase it is in."""

    ask: DecisionId
    agent: str
    scope: Scope
    revision: RevisionRef | None
    until: Within
    input: I
    """What the next rendered prompt is made from (the ask, a correction or a resume)."""
    attempt: int = Field(ge=0)
    """Counts the renders of this ask; it numbers the kernel decisions of the current one."""
    serial: int = Field(ge=0)
    charge: Charge
    invocation: InvocationRef | None = None
    """The turn in flight, once its prompt is rendered and the turn built."""
    predecessor: InvocationRef | None = None
    resume: InvocationRef | None = None
    """For a resume: the invocation core authorized, which the turn must use."""
    continuation: ContinuationId | None = None
    corrections: int = Field(default=0, ge=0)
    drops: int = Field(default=0, ge=0)
    phase: FlowPhase = Rendering()


class Serial(Value):
    """The next invocation serial of one role in one subject."""

    key: str
    next: int = Field(ge=0)


class Piped[S: StrategyState, I: Value](StrategyState):
    """The wrapped search's state and the asks in flight."""

    inner: S
    flows: tuple[Flow[I], ...] = ()
    serials: tuple[Serial, ...] = ()


def _digest(text: str) -> str:
    return sha256(" ".join(text.split()).encode()).hexdigest()


def reply_errors(error: ValidationError) -> tuple[ReplyError, ...]:
    """The typed problems of a reply that broke its model."""
    found: list[ReplyError] = []
    for item in error.errors():
        kind = item["type"]
        if kind == "missing":
            code = ReplyErrorCode.MISSING
        elif kind == "extra_forbidden":
            code = ReplyErrorCode.UNEXPECTED
        elif kind == "json_invalid":
            code = ReplyErrorCode.NOT_JSON
        elif kind.endswith(("_type", "_parsing")):
            code = ReplyErrorCode.WRONG_TYPE
        else:
            code = ReplyErrorCode.INVALID_VALUE
        path = tuple(part for part in item["loc"] if isinstance(part, str | int))
        found.append(ReplyError(path=path, code=code))
    return tuple(found) or (ReplyError(path=(), code=ReplyErrorCode.INVALID_VALUE),)


class AgentTurns[S: StrategyState, C]:
    """A search plus the pipeline that answers its ``AskAgent`` requests (itself a ``Search``)."""

    def __init__(  # noqa: PLR0913  # LW-220420 [PLR0913]; the pipeline is wired from its search, agents, protocol, operations, id scheme and input type, all distinct and keyword-only.
        self,
        inner: Search[S, C],
        *,
        agents: tuple[Agent, ...],
        protocol: AgentProtocol,
        operations: StrategyOperations,
        ids: IdScheme,
        inputs: object,
        render_limit: float = _RENDER_LIMIT,
    ) -> None:
        """Wrap ``inner``; ``inputs`` is the union type of the contexts it asks with."""
        self.inner = inner
        self.agents = {agent.role: agent for agent in agents}
        self.protocol = protocol
        self.operations = operations
        self.ids = ids
        self.render_limit = render_limit
        self.state_model = Piped[inner.state_model, inputs]  # ty: ignore[invalid-type-form]
        self._flow_type = Flow[inputs]  # ty: ignore[invalid-type-form]
        declaration = inner.declaration
        if WorkKind.ASK_AGENT not in declaration.uses:
            raise ContractError(("declaration", "uses"), "a search wrapped in agents asks agents")
        render = operations.schema_ref(f"{operations.namespace}.render_role_artifacts")
        self.declaration: SearchDeclaration = declaration.model_copy(
            update={
                "required_operations": (render, *declaration.required_operations),
                "uses": (declaration.uses - {WorkKind.ASK_AGENT})
                | {WorkKind.REQUEST_TURN, WorkKind.OPERATION, WorkKind.WAKE_AT},
                "handles": (declaration.handles - {EventKind.TURN_FINISHED})
                | PRODUCES[WorkKind.REQUEST_TURN]
                | {EventKind.OPERATION_FINISHED, EventKind.TIME_REACHED},
            }
        )

    # -- Search ---------------------------------------------------------------------------

    def init(self, config: C, facts: RunFacts) -> tuple[Piped[S, Value], list[Decision]]:
        """Start the wrapped search and lower what it asks."""
        state, decisions = self.inner.init(config, facts)
        piped = self.state_model(schema_version=state.schema_version, inner=state)
        return self._lower(piped, decisions)

    def update(
        self, state: Piped[S, Value], event: Event, view: RunView
    ) -> tuple[Piped[S, Value], list[Decision]]:
        """One event: the pipeline's own, or the wrapped search's."""
        state, decisions, deliveries = self._route(state, event, view)
        for delivered in deliveries:
            state, more = self._deliver(state, delivered, view)
            decisions = [*decisions, *more]
        return state, decisions

    # -- delivery to the wrapped search -----------------------------------------------------

    def _deliver(
        self, state: Piped[S, Value], event: Event, view: RunView
    ) -> tuple[Piped[S, Value], list[Decision]]:
        kind = EventKind(event.kind)
        if kind not in self.inner.declaration.handles and kind not in ALWAYS:
            return state, []
        inner, decisions = self.inner.update(state.inner, event, view)
        return self._lower(state.model_copy(update={"inner": inner}), decisions)

    def _lower(
        self, state: Piped[S, Value], decisions: list[Decision]
    ) -> tuple[Piped[S, Value], list[Decision]]:
        """Replace each ``AskAgent`` with the render it starts; pass everything else on."""
        lowered: list[Decision] = []
        for decision in decisions:
            if isinstance(decision, Wait) and isinstance(decision.request, AskAgent):
                state, wait = self._start(state, decision.request, decision.until)
                lowered.append(wait)
            else:
                lowered.append(decision)
        return state, lowered

    # -- routing ----------------------------------------------------------------------------

    def _route(  # noqa: C901, PLR0911, PLR0912  # LW-220421 [C901, PLR0911, PLR0912]; one dispatch over the flow phases, each arm returning its next flow, is the routing table.
        self, state: Piped[S, Value], event: Event, view: RunView
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        """Handle what belongs to a flow; whatever is not a flow's is the wrapped search's."""
        if isinstance(event, OperationFinished | TurnEnded | Failed):
            named = _named(event.decision)
            if named is None:
                return state, [], [event]
            flow = self._flow_of(state, named)
            if flow is None:
                return state, [], []  # a result for an ask that is over
            return self._answered(state, flow, event, view)
        match event:
            case TurnYielded(continuation=continuation):
                flow = self._flow_with(state, continuation.invocation, Yielding)
                if flow is None:
                    return state, [], []
                return self._replace(
                    state, flow, Suspended(continuation=continuation.continuation_id)
                )
            case YieldFailed(failure=failure):
                flow = self._flow_with(state, failure.invocation, Yielding)
                if flow is None:
                    return state, [], []
                refused = Failed(
                    reason=FailureReason.WAIT_REFUSED,
                    decision=flow.ask,
                    refusal=WaitRefusal.NOT_PUBLISHED,
                    turn=flow.invocation,
                )
                return self._end(state, flow, refused)
            case ResumeReady(authorization=authorization):
                waiting = next(
                    (
                        f
                        for f in state.flows
                        if isinstance(f.phase, Suspended)
                        and f.phase.continuation == authorization.continuation_id
                    ),
                    None,
                )
                if waiting is None:
                    return state, [], []
                return self._resume(state, waiting, authorization, view)
            case CheckpointKnown(invocation=invocation, checkpoint=checkpoint):
                holding = self._flow_with(state, invocation, Holding)
                if holding is None or not isinstance(holding.phase, Holding):
                    return state, [], []
                return self._finish(state, holding, holding.phase.output, checkpoint)
            case TimeReached():
                return self._wake(state, view, event)
            case _:
                return state, [], [event]

    def _wake(
        self, state: Piped[S, Value], view: RunView, event: Event
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        """Ask again every lost turn whose backoff has passed; the tick is also the search's."""
        decisions: list[Decision] = []
        for flow in state.flows:
            if isinstance(flow.phase, BackingOff) and flow.phase.until <= view.now_at:
                state = self._swap(state, flow.model_copy(update={"phase": Rendering()}))
                decisions.append(self._render(flow))
        return state, decisions, [event]

    # -- flows ------------------------------------------------------------------------------

    @staticmethod
    def _flow_of(state: Piped[S, Value], named: tuple[DecisionId, str, int]) -> Flow[Value] | None:
        ask, step, attempt = named
        flow = next((f for f in state.flows if f.ask == ask), None)
        if flow is None or flow.attempt != attempt:
            return None
        if step == "r" and not isinstance(flow.phase, Rendering):
            return None
        if step == "t" and not isinstance(flow.phase, Asked):
            return None
        return flow

    @staticmethod
    def _flow_with(
        state: Piped[S, Value], invocation: InvocationRef, phase: type[Value]
    ) -> Flow[Value] | None:
        return next(
            (f for f in state.flows if f.invocation == invocation and isinstance(f.phase, phase)),
            None,
        )

    @staticmethod
    def _swap(state: Piped[S, Value], flow: Flow[Value]) -> Piped[S, Value]:
        flows = tuple(flow if f.ask == flow.ask else f for f in state.flows)
        return state.model_copy(update={"flows": flows})

    def _replace(
        self, state: Piped[S, Value], flow: Flow[Value], phase: FlowPhase
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        return self._swap(state, flow.model_copy(update={"phase": phase})), [], []

    @staticmethod
    def _end(
        state: Piped[S, Value], flow: Flow[Value], result: Event
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        """The ask is over: forget it and hand the search its one result."""
        flows = tuple(f for f in state.flows if f.ask != flow.ask)
        return state.model_copy(update={"flows": flows}), [], [result]

    def _finish(
        self,
        state: Piped[S, Value],
        flow: Flow[Value],
        output: str,
        checkpoint: Checkpoint | None,
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        agent = self.agents[flow.agent]
        parsed = agent.reply.model_validate_json(output)
        reply = parsed.reply if isinstance(parsed, WaitableReply) else parsed
        if flow.invocation is None:
            raise ContractError(("flow", "invocation"), "a finished ask has run a turn")
        done = TurnFinished(
            decision=flow.ask,
            invocation=flow.invocation,
            reply=reply,
            checkpoint=checkpoint,
        )
        return self._end(state, flow, done)

    # -- starting a turn -----------------------------------------------------------------------

    @staticmethod
    def _subject(ask: DecisionId) -> str:
        return ask.root.rpartition("/")[0] or "run"

    def _serial(
        self, state: Piped[S, Value], role: str, ask: DecisionId
    ) -> tuple[Piped[S, Value], int]:
        key = f"{role}:{self._subject(ask)}"
        found = next((item for item in state.serials if item.key == key), None)
        number = 0 if found is None else found.next
        kept = tuple(item for item in state.serials if item.key != key)
        updated = (*kept, Serial(key=key, next=number + 1))
        return state.model_copy(update={"serials": updated}), number

    def _start(
        self, state: Piped[S, Value], ask: AskAgent, until: Within
    ) -> tuple[Piped[S, Value], Wait]:
        agent = self.agents.get(ask.agent)
        if agent is None:
            raise ContractError(("ask", "agent"), "no such agent")
        if any(f.ask == ask.decision_id for f in state.flows):
            raise ContractError(("ask", "decision_id"), "this ask is already in flight")
        state, serial = self._serial(state, agent.role, ask.decision_id)
        follows = ask.input.of if isinstance(ask.input, Correction) else None
        new = self._flow_type(
            ask=ask.decision_id,
            agent=agent.role,
            scope=ask.scope,
            revision=ask.revision,
            until=until,
            input=ask.input,
            attempt=0,
            serial=serial,
            charge="correction" if follows is not None else agent.charge,
            predecessor=follows,
        )
        state = state.model_copy(update={"flows": (*state.flows, new)})
        return state, self._render(new)

    def _render(self, flow: Flow[Value]) -> Wait:
        """Ask the renderer for the prompt of the flow's next turn."""
        subject = f"{self._subject(flow.ask)}.{flow.agent}"
        request = self.operations.render(subject=subject, ordinal=flow.serial, context=flow.input)  # ty: ignore[missing-argument]
        return Wait(
            request=Operation(
                decision_id=_decision(flow.ask, "r", flow.attempt),
                scope=flow.scope,
                request=request,
            ),
            until=Within(limit=self.render_limit, watches=flow.until.watches),
        )

    def _build(
        self, flow: Flow[Value], rendered: RenderedArtifacts, view: RunView
    ) -> tuple[Flow[Value], Wait]:
        """The kernel turn for a rendered prompt."""
        agent = self.agents[flow.agent]
        scope = view.run if flow.scope == THIS_RUN else flow.scope
        subject = self._subject(flow.ask)
        session = SessionSpec(
            session_id=self.ids.session_id(agent.role, subject),
            role_id=self.ids.role_id(agent.role),
            policy="reuse",
            lifetime="owner",
            access=agent.access,
        )
        invocation_id = (
            flow.resume.invocation_id
            if flow.resume is not None
            else self.ids.invocation_id(agent.role, subject, flow.serial)
        )
        invocation = InvocationRef(
            session_id=session.session_id,
            invocation_id=invocation_id,
            generation=scope.generation,
        )
        if agent.workspace is None:
            workspace: WorkspaceRef | Scope = scope
        elif flow.revision is None:
            raise ContractError(("ask", "revision"), "this agent works on a revision")
        else:
            workspace = WorkspaceRef(scope=scope, revision=flow.revision, mode=agent.workspace)
        spec = TurnSpec(
            session=session,
            invocation_id=invocation_id,
            continuation_id=flow.continuation,
            workspace=workspace,
            prompts=rendered.prompts,
            output_schema=agent.schema,
            tool_policy=rendered.tool_policy,
            charge_class=flow.charge,
            # A resume names its continuation, never a predecessor: core rejects both together.
            predecessor=None if flow.charge == "resume" else flow.predecessor,
        )
        asked = flow.model_copy(update={"invocation": invocation, "phase": Asked()})
        request = RequestTurn(
            decision_id=_decision(flow.ask, "t", flow.attempt), scope=scope, turn=spec
        )
        within = Within(limit=flow.until.limit, watches=flow.until.watches)
        return asked, Wait(request=request, until=within)

    # -- answers ----------------------------------------------------------------------------

    def _answered(
        self,
        state: Piped[S, Value],
        flow: Flow[Value],
        event: OperationFinished | TurnEnded | Failed,
        view: RunView,
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        match event:
            case Failed():
                return self._end(state, flow, event.model_copy(update={"decision": flow.ask}))
            case OperationFinished(result=result):
                outcome = result.outcome
                if (
                    not isinstance(outcome, RenderedArtifacts)
                    or outcome.status != "succeeded"
                    or not outcome.prompts
                ):
                    failed = Failed(reason=FailureReason.RENDER_FAILED, decision=flow.ask)
                    return self._end(state, flow, failed)
                asked, wait = self._build(flow, outcome, view)
                return self._swap(state, asked), [wait], []
            case TurnEnded(result=result):
                return self._ended(state, flow, result, view)

    def _ended(  # noqa: PLR0911  # LW-220422 [PLR0911]; one return per way a turn can end keeps the mapping to the next flow phase exhaustive.
        self, state: Piped[S, Value], flow: Flow[Value], result: TurnResult, view: RunView
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        agent = self.agents[flow.agent]
        turn = result.invocation
        output = result.output_json
        if result.observation.status is not ObservationStatus.SUCCEEDED or output is None:
            if result.failure is TurnFailureKind.TRANSPORT_LOST:
                return self._lost(state, flow, turn, view)
            failed = Failed(
                reason=FailureReason.TURN_FAILED,
                decision=flow.ask,
                turn=turn,
                turn_failure=result.failure or TurnFailureKind.PROVIDER_FAILED,
                digest=_digest(result.detail),
            )
            return self._end(state, flow, failed)
        flow = flow.model_copy(update={"invocation": turn})
        if result.suspending:
            # Core recorded the wait: the reply is dropped and the agent answers when resumed.
            if "suspend" not in view.capabilities.lifecycle or not agent.waits:
                refused = Failed(
                    reason=FailureReason.WAIT_REFUSED,
                    decision=flow.ask,
                    turn=turn,
                    refusal=WaitRefusal.NOT_OFFERED,
                )
                return self._end(state, flow, refused)
            return self._replace(state, flow, Yielding())
        try:
            parsed = agent.reply.model_validate_json(output)
        except ValidationError as error:
            errors = reply_errors(error)
            invalid = Failed(
                reason=FailureReason.INVALID_REPLY, decision=flow.ask, errors=errors, turn=turn
            )
            context = self.protocol.correction(agent.role, errors)
            return self._corrected(state, flow, context, invalid)
        reply = parsed.reply if isinstance(parsed, WaitableReply) else parsed
        if isinstance(reply, WaitingForDependency):
            refused = Failed(
                reason=FailureReason.WAIT_REFUSED,
                decision=flow.ask,
                turn=turn,
                refusal=WaitRefusal.NOT_RECORDED,
            )
            return self._corrected(state, flow, self.protocol.wait_unrecorded(agent.role), refused)
        if agent.checkpoint is None or not agent.checkpoint(reply):
            return self._finish(state, flow, output, None)
        known = _checkpoint_of(view, flow.scope, turn)
        if known is None:
            return self._replace(state, flow, Holding(output=output))
        return self._finish(state, flow, output, known)

    def _corrected(
        self,
        state: Piped[S, Value],
        flow: Flow[Value],
        context: Value,
        exhausted: Failed,
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        """Ask the agent to fix its reply, or report ``exhausted`` once its corrections are spent."""
        agent = self.agents[flow.agent]
        if flow.corrections >= agent.retry.corrections:
            return self._end(state, flow, exhausted)
        state, serial = self._serial(state, agent.role, flow.ask)
        again = flow.model_copy(
            update={
                "input": context,
                "attempt": flow.attempt + 1,
                "serial": serial,
                "charge": "correction",
                "predecessor": flow.invocation,
                "invocation": None,
                "resume": None,
                "continuation": None,
                "corrections": flow.corrections + 1,
                "drops": 0,
                "phase": Rendering(),
            }
        )
        return self._swap(state, again), [self._render(again)], []

    def _lost(
        self, state: Piped[S, Value], flow: Flow[Value], lost: InvocationRef, view: RunView
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        """The transport lost the turn: ask the same question again after a backoff, or report it."""
        agent = self.agents[flow.agent]
        if flow.charge == "resume" or flow.drops >= agent.retry.lost_turns:
            failed = Failed(reason=FailureReason.LOST, decision=flow.ask, turn=lost)
            return self._end(state, flow, failed)
        state, serial = self._serial(state, agent.role, flow.ask)
        until = view.now_at + agent.retry.backoff.delay(flow.drops)
        again = flow.model_copy(
            update={
                "attempt": flow.attempt + 1,
                "serial": serial,
                "charge": "correction",
                "predecessor": lost,
                "invocation": None,
                "drops": flow.drops + 1,
                "phase": BackingOff(until=until),
            }
        )
        return self._swap(state, again), [WakeAt(time=until)], []

    def _resume(
        self,
        state: Piped[S, Value],
        flow: Flow[Value],
        authorized: ResumeAuthorized,
        view: RunView,
    ) -> tuple[Piped[S, Value], list[Decision], list[Event]]:
        """Core authorized the resume of a suspended turn: ask it to continue."""
        agent = self.agents[flow.agent]
        retained = (
            None if flow.invocation is None else _revision_of(view, flow.scope, flow.invocation)
        )
        facts = ResumeFacts(
            agent=agent.role,
            retained=retained,
            timed_out=authorized.timeout is not None,
            evidence=authorized.evidence,
            repeated_failure=authorized.repeated_failure,
        )
        state, serial = self._serial(state, agent.role, flow.ask)
        resumed = flow.model_copy(
            update={
                "input": self.protocol.resume(facts),
                "attempt": flow.attempt + 1,
                "serial": serial,
                "charge": "resume",
                "predecessor": None,
                "resume": authorized.next_invocation,
                "continuation": authorized.continuation_id,
                "drops": 0,
                "phase": Rendering(),
            }
        )
        return self._swap(state, resumed), [self._render(resumed)], []


def _decision(ask: DecisionId, step: Literal["r", "t"], attempt: int) -> DecisionId:
    return DecisionId(root=f"{ask.root}{_SEPARATOR}{step}{attempt}")


def _named(decision: DecisionId | None) -> tuple[DecisionId, str, int] | None:
    """The ask, step and attempt a pipeline decision names; None for the search's own."""
    if decision is None:
        return None
    head, separator, tail = decision.root.rpartition(_SEPARATOR)
    if (
        not separator
        or len(tail) < _MIN_ASK_SUFFIX
        or tail[0] not in "rt"
        or not tail[1:].isdigit()
    ):
        return None
    return DecisionId(root=head), tail[0], int(tail[1:])


def _attempt_view(view: RunView, scope: Scope) -> AttemptView | None:
    if not isinstance(scope.owner, AttemptId):
        return None
    return next((a for a in view.attempts if a.attempt_id == scope.owner), None)


def _revision_of(view: RunView, scope: Scope, invocation: InvocationRef) -> RevisionRef | None:
    live = _attempt_view(view, scope)
    mine = () if live is None else tuple(c for c in live.checkpoints if c.invocation == invocation)
    return mine[-1].revision if mine else None


def _checkpoint_of(view: RunView, scope: Scope, invocation: InvocationRef) -> Checkpoint | None:
    """Core's outcome for the invocation's checkpoint, or None while it is still pending."""
    live = _attempt_view(view, scope)
    if live is None:
        return None
    kept = tuple(c for c in live.checkpoints if c.invocation == invocation)
    if kept:
        return Retained(revision=kept[-1].revision)
    declined = tuple(d for d in live.checkpoint_declines if d.invocation == invocation)
    return Declined(reason=declined[-1].reason) if declined else None


__all__ = ["Agent", "AgentProtocol", "AgentTurns", "TimeReached", "WakeAt", "reply_errors"]
