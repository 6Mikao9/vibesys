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
  type Record as StoreRecord,
  TailResponseSchema,
} from '@vibesys/proto';
import {FakeClock} from '@vibesys/transport/testing';
import fc from 'fast-check';
import {Window} from 'happy-dom';
import {act} from 'react';
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
  expect(buttons.includes('Resume')).toBe(
    input.state === 'parked' || (input.state === 'failed' && input.resumable),
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
  const send = (records: ReturnType<typeof recordsOf>) =>
    act(async () => active().receive(packet(records)));
  try {
    if (availabilityOnly) {
      await render();
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
