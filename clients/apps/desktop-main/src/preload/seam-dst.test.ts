/** Wire status -> gateway policy -> main IPC -> preload -> renderer codec -> reader -> screen. */
import {expect, test} from 'bun:test';
import {readFileSync} from 'node:fs';
import {startGatewayRun} from '@vibesys/feature-run-page/renderer';
import {storeRunFixture} from '@vibesys/run-fixtures';
import {FakeSkeletonCache} from '@vibesys/run-skeleton/cache';
import {gatewayReader} from '@vibesys/run-skeleton/reader';
import {FakeRunTransport} from '@vibesys/run-skeleton/testing';
import {seedsAtDepth} from '@vibesys/store/testing';
import {gatewayWorld} from './seam-world.test.js';

const statuses: Array<{name: string; code: number; message: string; grpcMessage: string}> =
  JSON.parse(
    readFileSync(
      new URL('../../../../../proto/testdata/grpc_status/statuses.json', import.meta.url),
      'utf8',
    ),
  );
const SEED = {
  identity: {host: 'local' as const, runId: 'run-a'},
  title: {project: 'project', task: null},
  hostLabel: 'Local',
  unavailable: {},
};

const BOUNDED_CALLS = 8;

interface Observation {
  readonly error: string | null;
  readonly complete: boolean;
  readonly calls: number;
  /** Calls the host saw after ten more virtual minutes passed on a finished screen. */
  readonly callsAfterWaiting: number;
  /** The renderer-side outcome of one unary call against the same wire status. */
  readonly unary: {code: unknown; message: string} | undefined;
}

/** Drive one wire status through every hop for one scheduler seed. */
async function observe(row: (typeof statuses)[number], seed: number): Promise<Observation> {
  let calls = 0;
  const remote = new FakeRunTransport(storeRunFixture());
  const world = gatewayWorld(seed, call => {
    calls += 1;
    if (row.code !== 0) {
      call.trailers(String(row.code), row.grpcMessage);
      return;
    }
    if (call.method.endsWith('/Skeleton') || call.method.endsWith('/GetBlobs')) {
      void remote.unary(call.method, call.bytes, call.signal).then(bytes => {
        call.message(bytes);
        call.trailers('0', '');
      });
    } else {
      void (async () => {
        for await (const bytes of remote.stream(call.method, call.bytes, call.signal))
          call.message(bytes);
        call.trailers('0', '');
      })();
    }
  });
  const actor = startGatewayRun(
    {
      reader: gatewayReader(world.transport, new FakeSkeletonCache(), world.clock.schedule),
      setting: async () => true,
      watchSetting: () => () => {},
    },
    world.clock.schedule,
    {seed: SEED},
  );
  try {
    await world.schedule.run(
      new Promise<void>(resolve => {
        const subscription = actor.subscribe(snapshot => {
          if (snapshot.context.load?.complete === true || snapshot.context.error !== null) {
            subscription.unsubscribe();
            resolve();
          }
        });
      }),
    );
    const {error, load} = actor.getSnapshot().context;
    const screenCalls = calls;
    for (let minute = 0; minute < 10; minute += 1) {
      world.clock.advanceBy(60_000);
      await world.schedule.run(Promise.resolve());
    }
    const waitedCalls = calls;
    const outcome = world.bridge.unary(
      'local',
      '/vibesys.api.v1.HostService/Skeleton',
      new Uint8Array([10, 5, 114, 117, 110, 45, 97]),
    );
    const unary = await world.schedule.run(
      outcome.result.then(
        () => undefined,
        (error: {code?: number; status?: number; message: string}) => ({
          // The refusal's status is `code` on a GrpcError and `status` on the bridge's error.
          code: error.code ?? error.status,
          message: error.message,
        }),
      ),
    );
    return {
      error,
      complete: load?.complete === true,
      calls: screenCalls,
      callsAfterWaiting: waitedCalls,
      unary,
    };
  } finally {
    actor.stop();
    world.close();
  }
}

const SEEDS = seedsAtDepth([1, 7, 23]);
const failing = statuses.filter(row => row.code !== 0);

/** A property whose fix is an unmerged pull request fails now; it must flip when that PR lands. */
const pendingFix = (pullRequest: number, name: string, body: () => Promise<void>): void =>
  // The replay script runs these as plain tests on historical commits (scripts/replay_seam.py).
  (process.env['SEAM_REPLAY'] === '1' ? test : test.failing)(
    `[#${pullRequest} unmerged] ${name}`,
    body,
  );

/** One test per status keeps each below the per-test bound at nightly depth (300 seeds). */
for (const row of statuses) {
  test(`${row.name}: the run screen ends within a bounded number of calls`, async () => {
    for (const seed of SEEDS) {
      const seen = await observe(row, seed);
      const context = `seed ${seed}`;
      if (row.code === 0) {
        expect(seen.complete, context).toBe(true);
        expect(seen.error, context).toBeNull();
      } else {
        expect(seen.error, context).not.toBeNull();
        expect(seen.callsAfterWaiting, `${context}: calls while time passes`).toBe(seen.calls);
      }
      expect(seen.calls, context).toBeLessThanOrEqual(BOUNDED_CALLS);
    }
  });
}

for (const row of failing) {
  if (row.code === 5)
    pendingFix(
      3434,
      `${row.name} ends the screen as "Not on this host", never "Disconnected"`,
      async () => {
        for (const seed of SEEDS)
          expect((await observe(row, seed)).error, `seed ${seed}`).toBe('Not on this host');
      },
    );
  pendingFix(
    3434,
    `${row.name} keeps its gRPC code and decoded diagnostic across the preload`,
    async () => {
      for (const seed of SEEDS)
        expect((await observe(row, seed)).unary, `seed ${seed}`).toEqual({
          code: row.code,
          message: row.message,
        });
    },
  );
}
