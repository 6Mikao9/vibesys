import {expect, test} from 'bun:test';
import {create, fromBinary, toBinary} from '@bufbuild/protobuf';
import type {HostId} from '@vibesys/hosts';
import {
  ConvertSavedRunsResponseSchema,
  FailureCategory,
  MutateRequestSchema,
  MutateResponseSchema,
  OperationFailed,
  RunConversionStatus,
} from '@vibesys/proto';
import {registryRunConversion} from '../index.js';
import {RUN_CONVERSION_CALLS} from './registry-run-conversion.js';

const HOST: HostId = {kind: 'ssh', alias: 'host-a'};

type RunState = 'legacy' | 'converted' | 'running';

/** A host holding one project's runs; each call converts at most `perCall` stopped legacy runs. */
class FakeConvertingHost {
  readonly projects: string[] = [];
  calls = 0;
  constructor(
    readonly runs: Map<string, RunState>,
    readonly perCall: number,
  ) {}
  async checkout() {
    return '/checkout';
  }
  mutate = async (_host: HostId, bytes: Uint8Array): Promise<Uint8Array> => {
    this.calls++;
    const envelope = fromBinary(MutateRequestSchema, bytes);
    if (envelope.method.case !== 'convertSavedRuns') throw new Error('unexpected mutation');
    const request = envelope.method.value;
    this.projects.push(request.project);
    let alreadyConverted = 0;
    let nextRun = request.afterRun;
    const rows = [];
    let budget = this.perCall;
    for (const [runId, state] of [...this.runs].sort(([a], [b]) => a.localeCompare(b))) {
      if (runId <= request.afterRun) continue;
      if (state === 'legacy' && budget === 0) break;
      nextRun = runId;
      if (state === 'converted') alreadyConverted++;
      if (state === 'running') {
        rows.push({runId, status: RunConversionStatus.SKIPPED, reason: 'run is live'});
      } else if (state === 'legacy' && budget > 0) {
        budget--;
        this.runs.set(runId, 'converted');
        rows.push({runId, status: RunConversionStatus.CONVERTED, reason: ''});
      }
    }
    const complete = ![...this.runs.values()].includes('legacy');
    return toBinary(
      MutateResponseSchema,
      create(MutateResponseSchema, {
        result: {
          case: 'convertSavedRuns',
          value: create(ConvertSavedRunsResponseSchema, {
            requestId: request.requestId,
            projectRoot: '/project',
            runs: rows.slice(0, 100),
            unreported: Math.max(0, rows.length - 100),
            complete,
            alreadyConverted,
            nextRun,
          }),
        },
      }),
    );
  };
}

test('every stopped legacy run converts across calls and a running run is never converted', async () => {
  for (let legacy = 0; legacy <= 6; legacy++) {
    for (let perCall = 1; perCall <= 3; perCall++) {
      const runs = new Map<string, RunState>([['live', 'running']]);
      for (let index = 0; index < legacy; index++) runs.set(`old-${index}`, 'legacy');
      runs.set('new', 'converted');
      const host = new FakeConvertingHost(runs, perCall);

      const outcome = await registryRunConversion(host).convert(
        HOST,
        '/work/queue',
        new AbortController().signal,
      );

      expect(outcome.kind).toBe('converted');
      if (outcome.kind !== 'converted') return;
      expect(outcome.runs.filter(run => run.status === 'converted').map(run => run.runId)).toEqual(
        Array.from({length: legacy}, (_, index) => `old-${index}`),
      );
      expect(outcome.runs.some(run => run.runId === 'live' && run.status !== 'skipped')).toBe(
        false,
      );
      expect(runs.get('live')).toBe('running');
      expect(host.calls).toBe(Math.max(1, Math.ceil(legacy / perCall)));
      expect(new Set(host.projects)).toEqual(new Set(['/work/queue']));
    }
  }
});

test('a host whose checkout predates the operation needs an update; other failures throw', async () => {
  for (const category of Object.values(FailureCategory).filter(
    (value): value is FailureCategory => typeof value === 'number',
  )) {
    const conversion = registryRunConversion({
      checkout: async () => '/checkout',
      mutate: async () =>
        toBinary(
          MutateResponseSchema,
          create(MutateResponseSchema, {
            result: {case: 'failure', value: {category, message: 'exit status 2'}},
          }),
        ),
    });
    const outcome = conversion
      .convert(HOST, '/work/queue', new AbortController().signal)
      .catch((error: unknown) => error);
    if (category === FailureCategory.UNSUPPORTED) {
      expect(await outcome).toEqual({kind: 'needs-update'});
    } else {
      expect(await outcome).toBeInstanceOf(OperationFailed);
    }
  }
});

test('a host that never finishes is bounded by the call limit', async () => {
  const host = new FakeConvertingHost(new Map([['old', 'legacy']]), 0);
  await expect(
    registryRunConversion(host).convert(HOST, '/work/queue', new AbortController().signal),
  ).rejects.toThrow('did not finish');
  expect(host.calls).toBe(RUN_CONVERSION_CALLS);
});

test('the cursor passes a large skipped prefix once and counts existing runs once', async () => {
  const runs = new Map<string, RunState>();
  for (let index = 0; index < 60; index++) runs.set(`a-live-${index}`, 'running');
  runs.set('b-existing', 'converted');
  for (let index = 0; index < 12; index++)
    runs.set(`z-old-${index.toString().padStart(2, '0')}`, 'legacy');
  const host = new FakeConvertingHost(runs, 1);
  const result = await registryRunConversion(host).convert(
    HOST,
    '/project',
    new AbortController().signal,
  );
  expect(result.kind).toBe('converted');
  if (result.kind !== 'converted') return;
  expect(result.runs.filter(run => run.status === 'converted')).toHaveLength(12);
  expect(result.runs.filter(run => run.status === 'skipped')).toHaveLength(60);
  expect(result.alreadyConverted).toBe(1);
  expect(host.calls).toBe(12);
});

test('an abort after a bounded call prevents starting another call', async () => {
  const abort = new AbortController();
  const host = new FakeConvertingHost(new Map([['old', 'legacy']]), 0);
  const convert = registryRunConversion({
    checkout: () => host.checkout(),
    mutate: async (id, bytes) => {
      const response = await host.mutate(id, bytes);
      abort.abort();
      return response;
    },
  });
  await expect(convert.convert(HOST, '/project', abort.signal)).rejects.toThrow();
  expect(host.calls).toBe(1);
});

test('a final cannot-convert row preserves its typed reason', async () => {
  const conversion = registryRunConversion({
    checkout: async () => '/checkout',
    mutate: async (_host, bytes) => {
      const envelope = fromBinary(MutateRequestSchema, bytes);
      if (envelope.method.case !== 'convertSavedRuns') throw new Error('unexpected mutation');
      return toBinary(
        MutateResponseSchema,
        create(MutateResponseSchema, {
          result: {
            case: 'convertSavedRuns',
            value: create(ConvertSavedRunsResponseSchema, {
              requestId: envelope.method.value.requestId,
              projectRoot: '/project',
              complete: true,
              runs: [
                {
                  runId: 'old',
                  status: RunConversionStatus.CANNOT_CONVERT,
                  reason: 'cannot convert: agent: Field required',
                },
              ],
            }),
          },
        }),
      );
    },
  });
  const result = await conversion.convert(HOST, '/project', new AbortController().signal);
  expect(result.kind).toBe('converted');
  if (result.kind === 'converted')
    expect(result.runs).toEqual([
      {runId: 'old', status: 'cannot-convert', reason: 'cannot convert: agent: Field required'},
    ]);
});
