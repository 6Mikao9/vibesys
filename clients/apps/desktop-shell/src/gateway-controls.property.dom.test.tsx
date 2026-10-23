import {afterAll, beforeAll, expect, test} from 'bun:test';
import {create, fromBinary, toBinary} from '@bufbuild/protobuf';
import type {HostKey} from '@vibesys/hosts';
import {FakeGatewayBridge} from '@vibesys/ipc/testing';
import {
  DrainGoal,
  HostCallResponseSchema,
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
    if (method === this.input.methods.hostCall) return call;
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
    const alive = input.liveness === RunLiveness.ALIVE;
    expect(buttons.includes('Pause now')).toBe(live && alive);
    expect(buttons.includes('Pause')).toBe(
      live &&
        alive &&
        (state.case === 'starting' ||
          (state.case === 'running' && state.value.hold?.reason.case !== 'operator')),
    );
    expect(buttons.includes('Guidance')).toBe(state.case === 'running' && alive);
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

async function withPeer(
  peer: RunPeer,
  work: (container: HTMLElement, clock: FakeClock) => Promise<void>,
) {
  const clock = new FakeClock();
  const container = document.createElement('div');
  const root = createRoot(container);
  try {
    await act(async () =>
      root.render(
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
        />,
      ),
    );
    await work(container, clock);
  } finally {
    await act(async () => root.unmount());
    container.remove();
  }
  expect(peer.streams.every(stream => stream.cancelled)).toBe(true);
  expect(clock.pending).toBe(0);
}

const controlButtons = (container: HTMLElement) =>
  [...container.querySelectorAll('button')]
    .map(button => button.textContent ?? '')
    .filter(text => ['Pause', 'Pause now', 'Guidance', 'Resume'].includes(text));

const FAULT = fc.record({
  source: fc.constantFrom('liveness', 'lifecycle', 'history'),
  fault: fc.constantFrom('end', 'stall'),
  elapsed: fc.integer({min: 0, max: 31_000}),
});
type Fault = Generated<typeof FAULT>;

function followers(peer: RunPeer, source: Fault['source']) {
  return peer.streams.filter(stream => {
    if (stream.cancelled) return false;
    if (source === 'liveness') return stream.method === peer.input.methods.watchRuns;
    if (stream.method !== peer.input.methods.tail) return false;
    const request = fromBinary(TailRequestSchema, stream.bytes);
    return (
      !request.sessionId && (source === 'lifecycle' ? request.fromSeq > 0n : request.fromSeq === 0n)
    );
  });
}

function assertVisible(container: HTMLElement) {
  if (controlButtons(container).length === 0)
    expect(container.textContent).toMatch(
      /catching up|unknown|waiting for the host|not confirmed/i,
    );
}

test('property: independently ending or stalling streams cannot revive a dead writer and recover within one virtual minute', async () => {
  await fc.assert(
    fc.asyncProperty(
      LIVE,
      fc.array(FAULT, {minLength: 1, maxLength: 5}),
      async (lifecycle, faults) => {
        const peer = peerFor({
          lifecycle,
          liveness: RunLiveness.INTERRUPTED,
          epoch: 1,
          loading: false,
          history: [],
          modernHead: true,
        });
        await withPeer(peer, async (container, clock) => {
          for (const fault of faults) {
            const before = peer.streams.length;
            const old = followers(peer, fault.source);
            await act(async () => {
              if (fault.fault === 'end') for (const stream of old) stream.ended('disconnected');
            });
            expect(
              controlButtons(container).some(text =>
                ['Pause', 'Pause now', 'Guidance'].includes(text),
              ),
            ).toBe(false);
            assertVisible(container);
            await act(async () => clock.advanceBy(fault.elapsed));
            expect(
              controlButtons(container).some(text =>
                ['Pause', 'Pause now', 'Guidance'].includes(text),
              ),
            ).toBe(false);
            assertVisible(container);
            await act(async () => clock.advanceBy(31_000));
            expect(peer.streams.length).toBeGreaterThan(before);
            assertVisible(container);
            const recovered = controlButtons(container);
            // Late bytes from retired subscriptions are fenced by their owning actor.
            await act(async () => {
              for (const stream of old.filter(stream => stream.cancelled)) {
                if (stream.method === peer.input.methods.watchRuns)
                  stream.receive(
                    toBinary(
                      WatchRunsResponseSchema,
                      create(WatchRunsResponseSchema, {
                        runId: 'run-a',
                        head: {epoch: 1},
                        liveness: RunLiveness.ALIVE,
                      }),
                    ),
                  );
              }
            });
            expect(controlButtons(container)).toEqual(recovered);
          }
        });
      },
    ),
    {
      numRuns: 16,
      seed: 34361,
      examples: [
        [
          create(LifecycleSchema, {state: {case: 'running', value: {}}}),
          [{source: 'liveness', fault: 'end', elapsed: 0}],
        ],
      ],
    },
  );
});

const GAP = fc.record({
  epoch: fc.integer({min: 2, max: 8}),
  lifecycle: LIVE,
  faults: fc.array(FAULT, {maxLength: 3}),
  lead: fc.constantFrom('history', 'liveness', 'ack'),
});

test('property: a successor on any independent stream exposes a stalled lifecycle and re-reads it within one virtual minute', async () => {
  await fc.assert(
    fc.asyncProperty(GAP, async ({epoch, lifecycle, faults, lead}) => {
      const peer = peerFor({
        lifecycle: create(LifecycleSchema, {state: {case: 'parked', value: {}}}),
        liveness: RunLiveness.ENDED,
        epoch: 1,
        loading: false,
        history: [],
        modernHead: true,
      });
      await withPeer(peer, async (container, clock) => {
        const sequence = peer.records.at(-1)?.seq ?? 0n;
        const header = peer.records.find(record => record.body.case === 'epochStarted');
        if (header?.body.case !== 'epochStarted') throw new Error('epoch header required');
        const successor = create(RecordSchema, {
          seq: sequence + 1n,
          epoch,
          body: {case: 'epochStarted', value: {...header.body.value, epoch}},
        });
        const next = create(RecordSchema, {
          seq: sequence + 2n,
          epoch,
          body: {case: 'lifecycle', value: lifecycle},
        });
        const snapshot = wire(
          {
            ...storeRunFixture('live'),
            records: [...peer.records, successor, next],
            initialCount: peer.records.length + 2,
          },
          RunLiveness.ALIVE,
        );
        // The host has the successor, while the already-open lifecycle follower stalls.
        peer.input.skeleton = snapshot.skeleton;
        peer.input.watchRuns = snapshot.watchRuns;
        const before = peer.calls.filter(
          call => call.method === peer.input.methods.skeleton,
        ).length;
        await act(async () => deliverLead(peer, container, lead, successor, snapshot));
        if (lead !== 'liveness') {
          expect(controlButtons(container)).toEqual([]);
          expect(container.textContent).toMatch(/catching up/i);
        }
        assertVisible(container);
        for (const fault of faults) {
          await act(async () => {
            if (fault.fault === 'end')
              for (const stream of followers(peer, fault.source)) stream.ended('disconnected');
          });
          assertVisible(container);
          await act(async () => clock.advanceBy(fault.elapsed));
          assertVisible(container);
        }
        await act(async () => clock.advanceBy(31_000));
        expect(
          peer.calls.filter(call => call.method === peer.input.methods.skeleton).length,
        ).toBeGreaterThan(before);
        await act(async () => {
          clock.advanceBy(1000);
          for (const stream of followers(peer, 'liveness'))
            stream.receive(new Uint8Array(snapshot.watchRuns));
        });
        const buttons = controlButtons(container);
        expect(buttons).toContain('Pause now');
        expect(buttons).not.toContain('Resume');
        assertVisible(container);
      });
    }),
    {
      numRuns: 16,
      seed: 34362,
      examples: [
        [
          {
            epoch: 2,
            lifecycle: create(LifecycleSchema, {state: {case: 'running', value: {}}}),
            faults: [],
            lead: 'history',
          },
        ],
      ],
    },
  );
});

function deliverLead(
  peer: RunPeer,
  container: HTMLElement,
  lead: 'history' | 'liveness' | 'ack',
  successor: StoreRecord,
  snapshot: WireFixture,
) {
  if (lead === 'history')
    for (const stream of followers(peer, 'history'))
      stream.receive(
        toBinary(
          TailResponseSchema,
          create(TailResponseSchema, {records: [toBinary(RecordSchema, successor)]}),
        ),
      );
  else if (lead === 'liveness')
    for (const stream of followers(peer, 'liveness'))
      stream.receive(new Uint8Array(snapshot.watchRuns));
  else {
    const resume = [...container.querySelectorAll('button')].find(
      button => button.textContent === 'Resume',
    );
    if (!resume) throw new Error('Resume required');
    resume.click();
    peer.calls.at(-1)?.resolve(
      toBinary(
        HostCallResponseSchema,
        create(HostCallResponseSchema, {
          result: {
            case: 'relaunch',
            value: {outcome: {case: 'epoch', value: BigInt(successor.epoch)}},
          },
        }),
      ),
    );
  }
}
