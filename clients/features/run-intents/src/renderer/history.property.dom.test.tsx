import {afterAll, beforeAll, expect, test} from 'bun:test';
import {create, fromBinary, toBinary} from '@bufbuild/protobuf';
import {type HostKey, recordedProjectRoot} from '@vibesys/hosts';
import {FakeGatewayBridge} from '@vibesys/ipc/testing';
import {
  DrainGoal,
  HostCallRequestSchema,
  HostCallResponseSchema,
  IntentAction,
  IntentStep,
  LifecycleSchema,
  Outcome,
  RecordSchema,
  RelaunchRefusal,
  RunLiveness,
  type Record as StoreRecord,
  TailResponseSchema,
  WatchRunsResponseSchema,
} from '@vibesys/proto';
import {FakeClock} from '@vibesys/transport/testing';
import fc from 'fast-check';
import {Window} from 'happy-dom';
import {act, useState} from 'react';
import {createRoot} from 'react-dom/client';
import {RunIntents} from './index.js';

const window = new Window();
const installed = Object.getOwnPropertyNames(window).filter(name => !(name in globalThis));
beforeAll(() => {
  for (const name of installed)
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value: Reflect.get(window, name),
    });
  Object.assign(globalThis, {window, IS_REACT_ACT_ENVIRONMENT: true});
});
afterAll(async () => {
  for (const name of [...installed, 'window', 'IS_REACT_ACT_ENVIRONMENT'])
    Reflect.deleteProperty(globalThis, name);
  await window.happyDOM.close();
});

const INPUT = fc.record({
  epoch: fc.integer({min: 1, max: 4}),
  state: fc.constantFrom('failed', 'finished', 'starting', 'running', 'draining', 'parked'),
  resumable: fc.boolean(),
  history: fc.array(
    fc.record({
      action: fc.constantFrom(
        IntentAction.PAUSE,
        IntentAction.PAUSE_NOW,
        IntentAction.RESUME,
        IntentAction.STEER,
      ),
      applied: fc.boolean(),
    }),
    {minLength: 1, maxLength: 8},
  ),
  order: fc.array(fc.nat({max: 100}), {minLength: 8, maxLength: 8}),
  reconnectAt: fc.integer({min: 0, max: 7}),
  remount: fc.boolean(),
  confirmed: fc.boolean(),
});
type Input = typeof INPUT extends fc.Arbitrary<infer Value> ? Value : never;
const LABELS = new Map([
  [IntentAction.PAUSE, 'Pause'],
  [IntentAction.PAUSE_NOW, 'Pause now'],
  [IntentAction.RESUME, 'Resume'],
  [IntentAction.STEER, 'Guidance'],
]);

function actionsOf(input: Input) {
  return input.history.map((item, index) => ({
    ...item,
    id: `control-${index}`,
    epoch: Math.min(input.epoch, Math.floor((index * input.epoch) / input.history.length) + 1),
  }));
}
function recordsOf(input: Input) {
  const records: StoreRecord[] = [];
  const actions = actionsOf(input);
  let sequence = 0n;
  for (let epoch = 1; epoch <= input.epoch; epoch += 1) {
    records.push(
      create(RecordSchema, {seq: ++sequence, epoch, body: {case: 'epochStarted', value: {epoch}}}),
    );
    for (const item of actions.filter(item => item.epoch === epoch))
      records.push(
        create(RecordSchema, {
          seq: ++sequence,
          epoch,
          body: {
            case: 'intent',
            value: {
              controlId: item.id,
              action: item.action,
              step: item.applied ? IntentStep.APPLIED : IntentStep.REFUSED,
              ...(!item.applied ? {reason: 'Run ended'} : {}),
            },
          },
        }),
      );
  }
  return records;
}
function historyWords(input: Input): string[] {
  const actions = actionsOf(input);
  const rows = [];
  for (let epoch = 1; epoch <= input.epoch; epoch += 1) {
    const current = actions.filter(item => item.epoch === epoch);
    if (epoch > 1 && !current.some(item => item.action === IntentAction.RESUME && item.applied))
      rows.push({id: `epoch-${epoch}`, epoch, action: IntentAction.RESUME, applied: true});
    rows.push(...current);
  }
  const latest = rows.filter(row => row.epoch === input.epoch && row.applied).at(-1);
  return rows.map(row => {
    const past =
      row.epoch < input.epoch
        ? ' · Earlier epoch'
        : row.applied && row !== latest
          ? ' · Earlier action'
          : '';
    return `${LABELS.get(row.action)} · ${row.applied ? 'Applied' : 'Refused'}${past}`;
  });
}
function packet(records: ReturnType<typeof recordsOf>) {
  return toBinary(
    TailResponseSchema,
    create(TailResponseSchema, {records: records.map(record => toBinary(RecordSchema, record))}),
  );
}

function currentLifecycle(input: Input) {
  return create(LifecycleSchema, {
    state:
      input.state === 'finished' || input.state === 'failed'
        ? {
            case: 'ended',
            value: {
              outcome: input.state === 'finished' ? Outcome.FINISHED : Outcome.FAILED,
              resumable: input.resumable,
            },
          }
        : input.state === 'draining'
          ? {case: 'draining', value: {goal: DrainGoal.PARK}}
          : {case: input.state, value: {}},
  });
}
function historyRows(container: HTMLElement): string[] {
  return [...container.querySelectorAll('span')]
    .map(span => span.textContent ?? '')
    .filter(text => /^(Pause(?: now)?|Resume|Guidance) · (Applied|Refused)/.test(text));
}

function verifyPermissions(container: HTMLElement, input: Input) {
  const buttons = [...container.querySelectorAll('button')].map(button => button.textContent);
  const live = ['starting', 'running', 'draining'].includes(input.state);
  expect(buttons.includes('Pause now')).toBe(live);
  expect(buttons.includes('Pause')).toBe(live && input.state !== 'draining');
  expect(buttons.includes('Guidance')).toBe(input.state === 'running');
  // The host admits Resume after every ending; its ended flag is kept for older readers.
  expect(buttons.includes('Resume')).toBe(
    input.state === 'parked' || input.state === 'failed' || input.state === 'finished',
  );
}

function activeStream(bridge: FakeGatewayBridge) {
  const stream = bridge.streams
    .filter(stream => stream.method.endsWith('/Tail') && !stream.cancelled)
    .at(-1);
  if (!stream) throw new Error('Tail required');
  return stream;
}

function confirmsSuccessor(input: Input): boolean {
  return input.confirmed && input.epoch > 1;
}

async function verifyHistory(input: Input, availabilityOnly = false) {
  const bridge = new FakeGatewayBridge();
  const clock = new FakeClock();
  const container = document.createElement('div');
  const root = createRoot(container);
  let mount = 0;
  const render = (
    epoch = BigInt(input.epoch),
    lifecycle = currentLifecycle(input),
    replace = true,
  ) =>
    act(async () =>
      root.render(
        <RunIntents
          key={replace ? ++mount : mount}
          bridge={bridge}
          host={'local' as HostKey}
          projectRoot={recordedProjectRoot('/project')}
          runId="run-1"
          enabled
          schedule={clock.schedule}
          relaunch={{epoch, lifecycle}}
        />,
      ),
    );
  const active = () => activeStream(bridge);
  const send = async (records: ReturnType<typeof recordsOf>) => {
    await act(async () => active().receive(packet(records)));
    expect(
      historyRows(container).filter(text => text.endsWith('· Applied')).length,
    ).toBeLessThanOrEqual(1);
  };
  try {
    if (availabilityOnly) {
      await render();
      await act(async () =>
        bridge.streams
          .find(s => s.method.endsWith('/WatchRuns'))
          ?.receive(
            livenessPacket(
              input.epoch,
              ['starting', 'running', 'draining'].includes(input.state)
                ? RunLiveness.ALIVE
                : RunLiveness.ENDED,
            ),
          ),
      );
      verifyPermissions(container, input);
      return;
    }
    const records = recordsOf(input);
    if (confirmsSuccessor(input)) {
      await render(
        BigInt(input.epoch - 1),
        create(LifecycleSchema, {state: {case: 'parked', value: {}}}),
      );
      await send(records.filter(record => record.epoch < input.epoch));
      const resume = [...container.querySelectorAll('button')].find(
        button => button.textContent === 'Resume',
      );
      if (!resume) throw new Error('Resume required');
      await act(async () => resume.click());
      const call = bridge.calls.at(-1);
      if (!call) throw new Error('HostCall required');
      expect(fromBinary(HostCallRequestSchema, call.bytes).method.case).toBe('relaunch');
      await act(async () =>
        call.resolve(
          toBinary(
            HostCallResponseSchema,
            create(HostCallResponseSchema, {
              result: {
                case: 'relaunch',
                value: {outcome: {case: 'epoch', value: BigInt(input.epoch)}},
              },
            }),
          ),
        ),
      );
      expect(historyRows(container).filter(text => text.endsWith('· Applied'))).toEqual([
        'Resume · Applied',
      ]);
      await render(BigInt(input.epoch), currentLifecycle(input), false);
    } else await render();
    const order = records
      .map((record, index) => ({record, rank: input.order[index] ?? 0}))
      .sort((a, b) => a.rank - b.rank);
    for (const [index, {record}] of order.entries()) {
      await send([record]);
      await send([record]);
      if (index === input.reconnectAt) {
        const old = active();
        await act(async () => {
          old.ended('Connection closed');
          clock.advanceBy(1000);
        });
        expect(old.cancelled).toBe(true);
        if (input.remount) await render();
      }
    }
    await send(records);
    for (const record of [...records].reverse()) await send([record]);
    expect(historyRows(container)).toEqual(historyWords(input));
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
  expect(bridge.streams.every(stream => stream.cancelled)).toBe(true);
}

test('property: history and the latest applied action depend on writer positions across replays', async () => {
  await fc.assert(
    fc.asyncProperty(INPUT, input => verifyHistory(input)),
    {numRuns: 40, seed: 253},
  );
});

test('property: controls equal writer permissions for live, parked, failed and finished epochs', async () => {
  await fc.assert(
    fc.asyncProperty(INPUT, input => verifyHistory(input, true)),
    {numRuns: 32, seed: 3430},
  );
});

const KINDS = [
  'starting',
  'running',
  'operatorHold',
  'draining',
  'parked',
  'finished',
  'failed',
] as const;
type Kind = (typeof KINDS)[number];
const STEP = fc.oneof(
  fc.record({
    kind: fc.constant('lifecycle' as const),
    epoch: fc.integer({min: 1, max: 4}),
    state: fc.constantFrom(...KINDS),
    resumable: fc.boolean(),
  }),
  fc.record({
    kind: fc.constant('tail' as const),
    epoch: fc.integer({min: 1, max: 4}),
    action: fc.constantFrom(IntentAction.PAUSE, IntentAction.PAUSE_NOW, IntentAction.RESUME),
    applied: fc.boolean(),
  }),
  fc.record({
    kind: fc.constant('liveness' as const),
    epoch: fc.integer({min: 1, max: 4}),
    value: fc.constantFrom(
      RunLiveness.ALIVE,
      RunLiveness.INTERRUPTED,
      RunLiveness.ENDED,
      RunLiveness.UNSPECIFIED,
    ),
  }),
  fc.record({kind: fc.constant('ack' as const)}),
);
type Step = typeof STEP extends fc.Arbitrary<infer Value> ? Value : never;

function lifecycleOf(kind: Kind, resumable: boolean) {
  return create(LifecycleSchema, {
    state:
      kind === 'finished' || kind === 'failed'
        ? {
            case: 'ended',
            value: {outcome: kind === 'finished' ? Outcome.FINISHED : Outcome.FAILED, resumable},
          }
        : kind === 'parked'
          ? {case: 'parked', value: {}}
          : kind === 'draining'
            ? {case: 'draining', value: {goal: DrainGoal.PARK}}
            : kind === 'operatorHold'
              ? {case: 'running', value: {hold: {reason: {case: 'operator', value: {}}}}}
              : {case: kind, value: {}},
  });
}

/** What the host would admit given the newest facts, however the three streams interleaved. */
function expectedButtons(facts: {
  lifecycleEpoch: number;
  kind: Kind;
  liveness: {epoch: number; value: RunLiveness};
  tailEpoch: number;
}): string[] {
  const newest = Math.max(facts.lifecycleEpoch, facts.liveness.epoch, facts.tailEpoch);
  if (facts.lifecycleEpoch < newest) return [];
  const value = facts.liveness.epoch === newest ? facts.liveness.value : RunLiveness.UNSPECIFIED;
  const live = ['starting', 'running', 'operatorHold', 'draining'].includes(facts.kind);
  const buttons: string[] = [];
  if (live && value === RunLiveness.ALIVE) {
    if (facts.kind === 'starting' || facts.kind === 'running') buttons.push('Pause');
    buttons.push('Pause now');
    if (facts.kind === 'running' || facts.kind === 'operatorHold') buttons.push('Guidance');
  }
  const stopped = !live;
  if (value !== RunLiveness.ALIVE && (stopped || value === RunLiveness.INTERRUPTED))
    buttons.push('Resume');
  return buttons;
}

interface Facts {
  lifecycleEpoch: number;
  kind: Kind;
  resumable: boolean;
  liveness: {epoch: number; value: RunLiveness};
  tailEpoch: number;
}

function tailRecords(
  facts: Facts,
  step: Extract<Step, {kind: 'tail'}>,
  index: number,
  seq: bigint,
) {
  const epoch = Math.max(facts.tailEpoch, step.epoch);
  const records: StoreRecord[] = [];
  if (epoch > facts.tailEpoch)
    records.push(
      create(RecordSchema, {seq: seq + 1n, epoch, body: {case: 'epochStarted', value: {epoch}}}),
    );
  records.push(
    create(RecordSchema, {
      seq: seq + BigInt(records.length) + 1n,
      epoch,
      body: {
        case: 'intent',
        value: {
          controlId: `step-${index}`,
          action: step.action,
          step: step.applied ? IntentStep.APPLIED : IntentStep.REFUSED,
        },
      },
    }),
  );
  return records;
}

function livenessPacket(epoch: number, value: RunLiveness) {
  return toBinary(
    WatchRunsResponseSchema,
    create(WatchRunsResponseSchema, {runId: 'run-1', liveness: value, head: {epoch}}),
  );
}

/** Answer a Resume the user could press, if the screen offers one, with a refusal. */
async function acknowledge(container: HTMLElement, bridge: FakeGatewayBridge) {
  const resume = [...container.querySelectorAll('button')].find(
    button => button.textContent === 'Resume' && !button.disabled,
  );
  const before = bridge.calls.length;
  if (resume) await act(async () => resume.click());
  const call = bridge.calls.at(-1);
  if (!resume || bridge.calls.length === before || !call) return;
  const result = {
    case: 'relaunch' as const,
    value: {outcome: {case: 'refusal' as const, value: RelaunchRefusal.LOCKED}},
  };
  await act(async () =>
    call.resolve(toBinary(HostCallResponseSchema, create(HostCallResponseSchema, {result}))),
  );
}

async function verifyInterleaving(steps: readonly Step[]) {
  const bridge = new FakeGatewayBridge();
  const clock = new FakeClock();
  const container = document.createElement('div');
  const root = createRoot(container);
  const facts: Facts = {
    lifecycleEpoch: 1,
    kind: 'running',
    resumable: true,
    liveness: {epoch: 0, value: RunLiveness.UNSPECIFIED},
    tailEpoch: 0,
  };
  let seq = 0n;
  const render = () =>
    act(async () =>
      root.render(
        <RunIntents
          bridge={bridge}
          host={'local' as HostKey}
          projectRoot={recordedProjectRoot('/project')}
          runId="run-1"
          enabled
          schedule={clock.schedule}
          relaunch={{
            epoch: BigInt(facts.lifecycleEpoch),
            lifecycle: lifecycleOf(facts.kind, facts.resumable),
          }}
        />,
      ),
    );
  const stream = (suffix: string) => {
    const open = bridge.streams.filter(s => s.method.endsWith(suffix) && !s.cancelled).at(-1);
    if (!open) throw new Error(`${suffix} required`);
    return open;
  };
  const check = () => {
    const shown = [...container.querySelectorAll('button')]
      .map(button => button.textContent ?? '')
      .filter(text => ['Pause', 'Pause now', 'Guidance', 'Resume'].includes(text));
    expect(shown.sort()).toEqual(expectedButtons(facts).sort());
    expect(
      historyRows(container).filter(text => text.endsWith('· Applied')).length,
    ).toBeLessThanOrEqual(1);
  };
  // Each stream is ordered by itself; only the streams interleave with one another.
  const apply = async (step: Step, index: number) => {
    if (step.kind === 'lifecycle') {
      facts.lifecycleEpoch = Math.max(facts.lifecycleEpoch, step.epoch);
      facts.kind = step.state;
      facts.resumable = step.resumable;
      await render();
    } else if (step.kind === 'tail') {
      const records = tailRecords(facts, step, index, seq);
      seq = records.at(-1)?.seq ?? seq;
      facts.tailEpoch = Math.max(facts.tailEpoch, step.epoch);
      await act(async () => stream('/Tail').receive(packet(records)));
    } else if (step.kind === 'liveness') {
      facts.liveness = {epoch: Math.max(facts.liveness.epoch, step.epoch), value: step.value};
      await act(async () =>
        stream('/WatchRuns').receive(livenessPacket(facts.liveness.epoch, step.value)),
      );
    } else await acknowledge(container, bridge);
  };
  try {
    await render();
    check();
    for (const [index, step] of steps.entries()) {
      await apply(step, index);
      check();
    }
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
}

test('property: controls equal what the host admits across any interleaving of lifecycle, history, liveness and acknowledgement', async () => {
  await fc.assert(
    fc.asyncProperty(fc.array(STEP, {minLength: 1, maxLength: 8}), steps =>
      verifyInterleaving(steps),
    ),
    {numRuns: 60, seed: 3431},
  );
});

test('property: unresolved facts keep their recovery deadline across liveness expiry and resubscription', async () => {
  await fc.assert(
    fc.asyncProperty(
      fc.constantFrom('starting', 'running', 'operatorHold', 'draining'),
      fc.constantFrom('parked', 'finished', 'failed'),
      fc.integer({min: 1, max: 4}),
      fc.boolean(),
      async (initial, ending, epoch, resumable) => {
        const bridge = new FakeGatewayBridge();
        const clock = new FakeClock();
        const container = document.createElement('div');
        const root = createRoot(container);
        let refreshes = 0;
        function ObservedRun() {
          const [kind, setKind] = useState<Kind>(initial);
          return (
            <RunIntents
              bridge={bridge}
              host={'local' as HostKey}
              projectRoot={recordedProjectRoot('/project')}
              runId="run-1"
              enabled
              schedule={clock.schedule}
              relaunch={{epoch: BigInt(epoch), lifecycle: lifecycleOf(kind, resumable)}}
              refreshWriter={() => {
                refreshes += 1;
                setKind(ending);
              }}
            />
          );
        }
        const watch = () =>
          bridge.streams
            .filter(stream => stream.method.endsWith('/WatchRuns') && !stream.cancelled)
            .at(-1);
        const labels = () =>
          [...container.querySelectorAll('button')].map(button => button.textContent);
        const visible = () => {
          if (labels().length === 0) expect(container.textContent).toMatch(/catching up|unknown/i);
        };
        try {
          await act(async () => root.render(<ObservedRun />));
          let observed = watch();
          await act(async () => observed?.receive(livenessPacket(epoch, RunLiveness.ALIVE)));
          expect(labels().sort()).toEqual(
            expectedButtons({
              lifecycleEpoch: epoch,
              kind: initial,
              liveness: {epoch, value: RunLiveness.ALIVE},
              tailEpoch: 0,
            }).sort(),
          );
          await act(async () => observed?.receive(livenessPacket(epoch, RunLiveness.ENDED)));
          expect(labels()).toEqual([]);
          visible();
          // Separate event-loop tasks flush React between tied deadlines. One advanceBy over all
          // timers would batch the expiry and refresh, hiding cancellation of the refresh deadline.
          for (let step = 0; step < 12 && refreshes === 0; step += 1) {
            const next = clock.pendingDelays()[0];
            if (next === undefined || clock.now() + next > 60_000) break;
            await act(async () => {
              clock.runNext();
            });
            visible();
            const current = watch();
            if (current && current !== observed) {
              observed = current;
              await act(async () => current.receive(livenessPacket(epoch, RunLiveness.ENDED)));
              visible();
            }
          }
          expect(refreshes).toBeGreaterThan(0);
          expect(clock.now()).toBeLessThanOrEqual(60_000);
          expect(labels()).toEqual(['Resume']);
        } finally {
          await act(async () => root.unmount());
          container.remove();
        }
        expect(clock.pending).toBe(0);
        expect(bridge.streams.every(stream => stream.cancelled)).toBe(true);
      },
    ),
    {numRuns: 16, seed: 34363, examples: [['starting', 'parked', 1, false]]},
  );
});

test('a page with no lifecycle yet is not asked to re-read: its own loader owns the retries', async () => {
  const bridge = new FakeGatewayBridge();
  const clock = new FakeClock();
  const container = document.createElement('div');
  const root = createRoot(container);
  let refreshes = 0;
  try {
    await act(async () =>
      root.render(
        <RunIntents
          bridge={bridge}
          host={'local' as HostKey}
          projectRoot={recordedProjectRoot('/project')}
          runId="run-1"
          enabled
          schedule={clock.schedule}
          refreshWriter={() => {
            refreshes += 1;
          }}
        />,
      ),
    );
    for (let step = 0; step < 24; step += 1) {
      const next = clock.pendingDelays()[0];
      if (next === undefined) break;
      await act(async () => {
        clock.runNext();
      });
    }
    expect(refreshes).toBe(0);
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
});
