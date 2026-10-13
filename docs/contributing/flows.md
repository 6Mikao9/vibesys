# Flows: how orchestration is written

A search is written as a **flow**: ordinary `async def` code that reads top to bottom, using a
small vocabulary we own. The runtime (`vs_core.flows`) journals everything a flow observes and
replays it after a restart, so a flow survives crashes, and tests drive it with scripted outcomes
in milliseconds.

The design takes its principles from Effect (effects are data the runtime interprets; three kinds
of failure; policies as values), its semantics from Trio (structured groups, shielding,
cancellation only at `await`), and from RxJS the idea that what a flow waits for is data, built
from a few combinable sources. The surface is plain Python: `async`/`await`, `async with`,
`match`, pydantic values.

## The rule

There are two kinds of code.

- **Flow code** is an `async def` whose first argument is `ctx: Flow`. It may call `ctx`
  operations, other flow functions and pure functions, and use locals, loops, `match`, `try` and
  the `async with` constructs below. Nothing else: no `asyncio`, clock, randomness, threads, files,
  sockets or processes. Everything outside the flow reaches it through `ctx`.
- **Pure code** is ordinary Python that computes values (prompts, plan validation, settlement
  policy). No I/O, clock or randomness; tested with property tests.

Enforced by `tests/architecture/test_flow_code_is_restricted.py` (imports), lint (no blind
`except`), and the runtime (awaiting anything that is not a `ctx` operation raises at once).
A capability a flow lacks is added here, to the framework, and reviewed; never hand-rolled in a
search.

## The vocabulary

| Operation | What it does |
|---|---|
| `await ctx.wait(request, until, reply=M)` | Start a request and wait for its outcome |
| `ctx.start(request, until, reply=M)` | Start a request now; returns a `Pending` source to wait on later |
| `await ctx.first(*sources)` | The next value from whichever source is ready first |
| `async with ctx.group(limit=n) as g` | A scope for concurrent child flows |
| `async with my_resource(ctx, ...) as r` | Acquire and always release (`@resource`) |
| `await ctx.retry(attempt, policy)` | Repeat an attempt under a retry policy |
| `ctx.read(fn, as_)`, `ctx.now()`, `ctx.facts` | Read the run's view (journaled) |
| `ctx.timer(at)` | A source that fires at a run-clock time |
| `ctx.stopped`, `ctx.stop_requested` | The operator's stop, as a source and as a flag |
| `with ctx.shielded():` | A region a stop cannot interrupt |
| `ctx.publish(record)` | Make a typed record of the flow's progress visible outside |
| `await ctx.safe_point(snapshot)` | Where a code change may resume this flow |
| `await ctx.finish(result)` | End the run; returns only if core refuses |

### Effects: `wait` and `start`

```python
match await ctx.wait(ask_implementer(w, blockers), Within(limit=cfg.implementer_turn_seconds),
                     reply=ImplementerResult):
    case TurnFinished(reply=ImplementerResult() as reply): ...
    case Failed(reason=FailureReason.TIMED_OUT): ...
    case Failed() as failed: ...
```

Every effect states its own bound at the call that launches it (`Within`, with watches such as
`| WhileOpen(workspace)`). An outcome is a value, never an exception: `Failed` is matched like any
other result. `ctx.start(...)` is the same without waiting; it returns a `Pending` source.
Decision ids are assigned by the runtime from the request's label and the flow's path. The
runtime knows which event answers which request kind (a `Withdraw` is answered by the
`WorkspaceClosed` of its target attempt), so flows never route events.

### Sources and `first`

A **source** is anything a flow can wait on for a value:

| Source | Its values |
|---|---|
| `Pending` (from `ctx.start`) | the effect's outcome, once |
| `Task[T]` (from `group.start`) | the task's result, once |
| `Group[T]` | each finished task's result (or `TaskStopped(key)`), once each, in finishing order |
| `ctx.timer(at)` | `TimeUp(at)`, once |
| `ctx.stopped` | `StopRequested`, once |

**Every source yields each value once.** `await source` waits for its next value;
`await ctx.first(a, b, c)` waits for the next value of whichever is ready first, and returns it
(ties go to the earliest source in the argument list). Values carry their own types, so the
caller matches on them:

```python
match await ctx.first(planning, workstreams, ctx.stopped):
    case PlanOutcome() as outcome: ...
    case WorkstreamDone() as done: ...
    case TaskStopped(key=key): ...
    case StopRequested(): ...
```

What a flow is waiting for is therefore always data: the runtime can say "ws-3 waits for
implementer.2 or the stop" in logs and tests. A flow waiting only on sources that can never yield
again (an empty group, a consumed stop, nothing pending) is a bug, raised as `Deadlock` with the
flow and its sources named.

### Groups

```python
async with ctx.group(limit=config.max_in_flight) as workstreams:
    workstreams.start(start.key, workstream, start)     # returns Task[WorkstreamDone]
    workstreams.free                                    # slots left under the limit
    async for done in workstreams: ...                  # every result, as they finish
```

A child can never outlive its group: leaving the `async with` waits for every task still
running. `start` on a full group raises; read `free` first. `limit=1` is "one at a time" (a
second start while one runs is a bug, not a queue).

**Stops.** When the operator stops the run, every task in a group with `stops=True` (the default)
receives `Stopped` at its current `await`, once. A task that lets `Stopped` escape ends, and its
group yields `TaskStopped(key)` in its place; cleanup belongs in a resource (below), so most flows
never mention `Stopped`. A group with `stops=False` (for example a planner call whose reply the
search still wants) is not interrupted. A stop is raised before anything is issued: at an unshielded `wait`, `start` or `first`,
a pending stop raises `Stopped` first, so a task started in a stopping group (or one leaving a
shielded region) issues nothing more and needs no `if ctx.stop_requested` guard. The root flow is never interrupted; it sees `ctx.stopped` and `ctx.stop_requested`.

### Resources

A resource is written like `contextlib.asynccontextmanager`:

```python
@resource
async def attempt(ctx: Flow, w: WorkstreamStart) -> AsyncIterator[Attempt]:
    opened = await open_attempt(ctx, w)        # raises NotOpened if the workspace never opens
    try:
        yield opened
    except Stopped:
        await settle(ctx, opened, failure=Failure(kind=FailureKind.STOPPED))
    except WorkEnded as ended:
        await settle(ctx, opened, failure=ended.failure)
    else:
        await settle(ctx, opened)
```

Entering a resource is a checkpoint: a stop already pending for the task is raised at
`async with`, before the acquire runs, so a stopped task never acquires. The code before `yield`
and after it is shielded: a stop that arrives while the workspace opens
or settles waits, and is delivered at the first `await` inside the `async with` body. Whatever
way the body ends, the release runs: one construct that every exit passes through.

### Retry

```python
class Again[R](Value): reason: R             # "try again, because R"
class Tries[R](Value): number: int; reasons: tuple[R, ...]
class GaveUp[R](Value): reasons: tuple[R, ...]
class RetryPolicy(Value): max_tries: int; backoff: Backoff | None = None

result = await ctx.retry(lambda tries: implementer_turn(ctx, w, a, tries.reasons), policy)
```

The attempt returns a value (done) or `Again(reason)` (try again). The policy bounds the tries
and, with a backoff, waits on the run clock between them (journaled like any other wait).
`ctx.retry` returns the value or `GaveUp(reasons)`. Policies are configuration values.

### Reading the run

`ctx.read(fn, as_)` is the only way to read the run's view (clock, attempts, measurements,
settlements, capabilities); the value is journaled and decoded the same way live and in replay.
`ctx.now()` and `ctx.facts` are shorthands.

### Publishing progress

`ctx.publish(record)` replaces the flow's published record (a pydantic model): what status views,
projections and tests read about a run in flight. It is kept in the state beside the journal, not
in it. `FlowSearch.published(state, Record)` reads it.

### Safe points and code changes

A flow registered with `@flow(snapshot=S)` takes its snapshot as its argument and records newer
ones with `await ctx.safe_point(s)`. If a run resumes on changed code (the journal no longer
replays), every live flow restarts from its latest snapshot. `group.start(key, ...)` with the key
of a flow that has a live snapshot resumes that flow instead of starting it fresh, so a parent
restarted from its snapshot re-enters its group and re-starts its children by key. Work since the
last safe point is redone. Without registration, a code change raises `FlowCodeChanged`.

### Failures, three kinds

1. **Outcomes** are values: `Failed`, `GaveUp`, `TaskStopped`, a typed result. Match on them.
2. **Interruption** is only `Stopped`, delivered at an unshielded `await`.
3. **Defects** are everything else. A flow may raise its own typed exception to leave a deep call
   early (`WorkEnded`), but it must catch it itself; any exception that escapes a task, other than
   `Stopped`, crashes the run loudly. Flow code never uses a blind `except`.

## What dynamic looks like

```python
async def dynamic(ctx: Flow, config: DynamicConfig) -> None:
    baseline = await measure_input(ctx, config)
    run = RunRecord(config, baseline)
    async with (
        ctx.group(limit=config.max_in_flight) as workstreams,
        ctx.group(limit=1, stops=False) as planning,
    ):
        while not run.over(workstreams, planning, stopping=ctx.stop_requested):
            for start in run.ready_to_start(workstreams.free):
                workstreams.start(start.key, workstream, start)
            if planning.free and run.may_plan(workstreams.free, stopping=ctx.stop_requested):
                planning.start(run.next_call(), plan, run)
            match await ctx.first(planning, workstreams, ctx.stopped):
                case PlanOutcome() as outcome:
                    run.planned(outcome)
                case WorkstreamDone() as done:
                    run.finished(done)
                case TaskStopped(key=key):
                    run.stopped_before_start(key)
                case StopRequested():
                    pass  # the workstreams' group has stopped them
            ctx.publish(run.snapshot())
    await finish(ctx, run)


async def workstream(ctx: Flow, w: WorkstreamStart) -> WorkstreamDone:
    if not await parent_reproduces(ctx, w):
        return WorkstreamDone.unverified(w)
    try:
        async with attempt(ctx, w) as a:
            a.closing = await do_work(ctx, w, a)
    except NotOpened as failed:
        return WorkstreamDone.refunded(w, failed.failure)
    return a.done


async def implement_until_measurable(ctx: Flow, w: WorkstreamStart, a: Attempt) -> Result:
    match await ctx.retry(lambda tries: implementer_turn(ctx, w, a, tries.reasons), w.retries):
        case Result() as result:
            return result
        case GaveUp(reasons=blockers):
            raise WorkEnded(blocked_failure(blockers[-1]))
```

## Testing a flow

`vs_core.testing.flows.FlowRun` drives a flow search with scripted outcomes, no real time:

```python
def test_a_stop_while_implementing_settles_the_attempt_as_stopped(begin: Begin) -> None:
    run = begin(dynamic_search(config()))
    run.reply(run.expect(Measure), measured(throughput=100.0))
    run.reply(run.expect(AskAgent, label="planner"), plans("h1"))
    run.answer(run.expect(StartAttempt), opened())
    run.expect(AskAgent, label="implementer")
    run.stop()
    settle = run.expect(Withdraw)
    assert settle.disposition.outcome == "failed"
```

- `begin` runs every story twice: live, and with a save-and-restart after every step.
- `run.waiting()` says what each flow waits for (its sources), so a test can assert it.
- `run.timeline()` prints the run as a timeline (`planner:plans(1) ws-1:opened stop ws-1:settled`);
  property tests print their shrunk counterexamples this way.
- Pure helpers are tested without the driver; helpers that need only part of `ctx` take a role
  interface (`Waits`, `Reads`) and a small fake.

## Not in the vocabulary, on purpose

- Scope-wide deadlines (Trio's `move_on_after`): a bound belongs to the call that launches the
  work.
- Callbacks (`then=`), predicates (`until(lambda)`) and operator chains: waiting is on sources,
  and sequencing is `await`.
- A crash-loop budget: a run whose replay keeps failing is stopped by the shell, which counts
  restarts; the flow state cannot count replays that never finished.

## Implementation notes

Where the runtime refines the contract above:

- Operation results (`OperationFinished`) are journaled as JSON and rebuilt through the
  `OperationRegistry` given to `FlowSearch`; turn replies are rebuilt from the `reply=` model.
- Restart from snapshots is opt-in: `FlowSearch(..., flows=(worker, ...))` lists the `@flow`
  functions that may be resumed. `@flow(snapshot=S, name=...)` names a flow explicitly.
- `ctx.read` needs a view, so it cannot be called before the flow's first `await`.
- `Group.free` is a large constant when the group has no limit. `ctx.stopped` yields the
  `StopRequested` event itself. A stopped `Task` yields `TaskStopped(key)`.
- Awaiting inside a `finally` while the run itself is being discarded is not supported; use
  `except Stopped` (or `@resource`) for cleanup that awaits.
- `ctx.retry` infers `V | GaveUp[R]` when the attempt is a named function or closure. `ty`
  cannot infer `R` through a bare `lambda tries: ...`; give the attempt a name (or annotate the
  result).
