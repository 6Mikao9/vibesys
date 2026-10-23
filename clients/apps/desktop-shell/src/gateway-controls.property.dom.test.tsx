import {afterAll, beforeAll, expect, test} from 'bun:test';
import {create, fromBinary, toBinary} from '@bufbuild/protobuf';
import type {HostKey} from '@vibesys/hosts';
import {FakeGatewayBridge} from '@vibesys/ipc/testing';
import {
  DrainGoal,
  IntentAction,
  IntentStep,
  LifecycleSchema,
  Outcome,
  RecordSchema,
  RunLiveness,
  StageState,
  StartupStage,
  type Record as StoreRecord,
  TailRequestSchema,
  TailResponseSchema,
  WatchRunsResponseSchema,
} from '@vibesys/proto';
import {storeRunFixture} from '@vibesys/run-fixtures';
import {FakeClock} from '@vibesys/transport/testing';
import {IDBFactory} from 'fake-indexeddb';
import fc from 'fast-check';
import {Window} from 'happy-dom';
import {act, Profiler} from 'react';
import {createRoot} from 'react-dom/client';
import {GatewayRun} from './gateway-run.js';
import {type WireFixture, wire} from './testing/gateway-wire.js';

const window = new Window();
const installed = Object.getOwnPropertyNames(window).filter(name => !(name in globalThis));
beforeAll(() => {
  for (const name of installed)
    Object.defineProperty(globalThis, name, {
      configurable: true,
      writable: true,
      value: Reflect.get(window, name),
    });
  Object.assign(globalThis, {window, indexedDB: new IDBFactory(), IS_REACT_ACT_ENVIRONMENT: true});
});
afterAll(async () => {
  for (const name of [...installed, 'window', 'indexedDB', 'IS_REACT_ACT_ENVIRONMENT'])
    Reflect.deleteProperty(globalThis, name);
  await window.happyDOM.close();
});

/** The same protobuf peer as the app journeys, through the Electron byte bridge role. */
class RunPeer extends FakeGatewayBridge {
  constructor(
    readonly input: WireFixture,
    readonly records: StoreRecord[],
    readonly loading: boolean,
  ) {
    super();
  }
  override unary(host: HostKey, method: string, request: Uint8Array) {
    const call = super.unary(host, method, request);
    const reply =
      method === this.input.methods.skeleton
        ? this.input.skeleton
        : method === this.input.methods.blobs
          ? this.input.blobs[[...request].join(',')]
          : undefined;
    if (!reply) throw new Error(`Unexpected unary ${method}`);
    this.calls.at(-1)?.resolve(new Uint8Array(reply));
    return call;
  }
  override stream(
    host: HostKey,
    method: string,
    request: Uint8Array,
    receive: (bytes: Uint8Array) => void,
    ended: (error?: string) => void,
  ) {
    const owned = super.stream(host, method, request, receive, ended);
    if (method === this.input.methods.tail) {
      const tail = fromBinary(TailRequestSchema, request);
      const records = this.records.filter(record => record.seq >= tail.fromSeq);
      if (!tail.sessionId && records.length > 0 && !(this.loading && tail.fromSeq > 0n))
        receive(
          toBinary(
            TailResponseSchema,
            create(TailResponseSchema, {
              records: records.map(record => toBinary(RecordSchema, record)),
              throughSeq: records.at(-1)?.seq ?? 0n,
            }),
          ),
        );
    } else if (method === this.input.methods.watchRuns)
      receive(new Uint8Array(this.input.watchRuns));
    else if (method === this.input.methods.log) receive(new Uint8Array(this.input.log));
    else throw new Error(`Unexpected stream ${method}`);
    return owned;
  }
}

const LIVE = fc.constantFrom(
  create(LifecycleSchema, {
    state: {
      case: 'starting',
      value: {stage: StartupStage.PROBING_WORKSPACE, state: StageState.RUNNING},
    },
  }),
  create(LifecycleSchema, {state: {case: 'running', value: {}}}),
  create(LifecycleSchema, {
    state: {case: 'running', value: {hold: {reason: {case: 'operator', value: {}}}}},
  }),
  create(LifecycleSchema, {state: {case: 'draining', value: {goal: DrainGoal.PARK}}}),
);
const STATE = fc.oneof(
  LIVE,
  fc.constant(create(LifecycleSchema, {state: {case: 'parked', value: {}}})),
  fc
    .record({
      outcome: fc.constantFrom(
        Outcome.FINISHED,
        Outcome.FAILED,
        Outcome.STOPPED,
        Outcome.INTERRUPTED,
      ),
      resumable: fc.boolean(),
    })
    .map(value => create(LifecycleSchema, {state: {case: 'ended', value}})),
);

const BACKGROUND = fc.record({
  epoch: fc.integer({min: 1, max: 8}),
  modernHead: fc.boolean(),
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
    {maxLength: 6},
  ),
});
type Generated<A> = A extends fc.Arbitrary<infer Value> ? Value : never;
type Scenario = Generated<typeof BACKGROUND> & {
  lifecycle: Generated<typeof STATE>;
  liveness: RunLiveness;
  loading: boolean;
};
const LIVENESS = fc.constantFrom(
  RunLiveness.ALIVE,
  RunLiveness.INTERRUPTED,
  RunLiveness.ENDED,
  RunLiveness.UNSPECIFIED,
);

function peerFor({lifecycle, liveness, epoch, loading, history, modernHead}: Scenario): RunPeer {
  const source = storeRunFixture('live');
  const records = source.records.map(record =>
    create(RecordSchema, {
      ...record,
      epoch,
      body:
        record.body.case === 'epochStarted'
          ? {case: 'epochStarted', value: {...record.body.value, epoch}}
          : record.body.case === 'lifecycle'
            ? {case: 'lifecycle', value: lifecycle}
            : record.body,
    }),
  );
  const last = records.at(-1)?.seq ?? 0n;
  records.push(
    ...history.map((item, index) =>
      create(RecordSchema, {
        seq: last + BigInt(index + 1),
        epoch,
        body: {
          case: 'intent',
          value: {
            controlId: `history-${index}`,
            action: item.action,
            step: item.applied ? IntentStep.APPLIED : IntentStep.REFUSED,
            ...(!item.applied ? {reason: 'Unsupported by an older host'} : {}),
          },
        },
      }),
    ),
  );
  const fixture = {
    ...source,
    records,
    initialCount: loading ? source.initialCount : records.length,
  };
  const input = wire(fixture, liveness);
  if (modernHead) {
    const observed = fromBinary(WatchRunsResponseSchema, new Uint8Array(input.watchRuns));
    if (!observed.head) throw new Error('head required');
    observed.head.epoch = epoch;
    input.watchRuns = [...toBinary(WatchRunsResponseSchema, observed)];
  }
  return new RunPeer(input, records, loading);
}

const ADDRESS = {
  fragment: () => '',
  push: () => {},
  onRestore: () => () => {},
  onEscape: () => () => {},
};
async function verifyOffers(input: Scenario) {
  const peer = peerFor(input);
  const clock = new FakeClock();
  const container = document.createElement('div');
  const root = createRoot(container);
  let renders = 0;
  try {
    await act(async () =>
      root.render(
        <Profiler
          id="run"
          onRender={() => {
            renders += 1;
          }}
        >
          <GatewayRun
            seed={{
              identity: {host: 'local' as HostKey, runId: 'run-a'},
              title: {project: '/project', task: 'mpmc'},
              hostLabel: 'host-a',
              unavailable: {},
            }}
            bridge={peer}
            settings={{
              runTransportV2: async () => ({ok: true, value: true}),
              onHostSettingsChanged: () => () => {},
            }}
            schedule={clock.schedule}
            address={ADDRESS}
            legacy={() => null}
          />
        </Profiler>,
      ),
    );
    const buttons = [...container.querySelectorAll('button')].map(button => button.textContent);
    const state = input.lifecycle.state;
    const live = state.case === 'starting' || state.case === 'running' || state.case === 'draining';
    // What the host would admit: live actions need a writer that is not dead, and Resume needs
    // one that is not alive; every ending is admitted, whatever flag the ended record keeps.
    const dead = input.liveness === RunLiveness.INTERRUPTED;
    expect(buttons.includes('Pause now')).toBe(live && !dead);
    expect(buttons.includes('Pause')).toBe(
      live &&
        !dead &&
        (state.case === 'starting' ||
          (state.case === 'running' && state.value.hold?.reason.case !== 'operator')),
    );
    expect(buttons.includes('Guidance')).toBe(state.case === 'running' && !dead);
    expect(buttons.includes('Resume')).toBe(
      input.liveness !== RunLiveness.ALIVE && (!live || dead),
    );
    if (!input.loading) {
      expect(container.querySelector('[inert] button') === null).toBe(true);
    }
    expect(renders).toBeLessThan(40);
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
  expect(peer.streams.every(stream => stream.cancelled)).toBe(true);
}

test('property: every loading live run exposes the controls its writer admits through the real app bridge', async () => {
  await fc.assert(
    fc.asyncProperty(LIVE, LIVENESS, BACKGROUND, async (lifecycle, liveness, background) => {
      await verifyOffers({...background, lifecycle, liveness, loading: true});
    }),
    {numRuns: 24, seed: 226},
  );
});

test('property: ready run controls follow the writer lifecycle and liveness, including interrupted writers and every ended run', async () => {
  await fc.assert(
    fc.asyncProperty(STATE, LIVENESS, BACKGROUND, async (lifecycle, liveness, background) => {
      await verifyOffers({...background, lifecycle, liveness, loading: false});
    }),
    {numRuns: 32, seed: 229},
  );
});
