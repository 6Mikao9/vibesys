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
import {RUN_CONVERSION_CALLS, registryRunConversion} from './registry-run-conversion.js';

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
    const alreadyConverted = [...this.runs.values()].filter(state => state === 'converted').length;
    const rows = [];
    let budget = this.perCall;
    for (const [runId, state] of this.runs) {
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
            runs: rows,
            complete,
            alreadyConverted,
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
