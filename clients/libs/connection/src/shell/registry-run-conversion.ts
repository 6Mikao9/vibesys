/** Convert a project's stopped pre-transport runs on a host so the run list can read them. */
import type {HostId} from '@vibesys/hosts';
import {
  acceptConvertSavedRuns,
  type ConvertSavedRunsResponse,
  callConvertSavedRuns,
  decodeHostMutate,
  encodeHostMutate,
  FailureCategory,
  OperationFailed,
  type OperationSignal,
  operationPolicies,
  type RunConversion,
  RunConversionStatus,
} from '@vibesys/proto';
import {newRequestId} from '@vibesys/request-identity';
import {requireCheckout} from './registry-reads.js';

export interface RegistryRunConversionPorts {
  checkout(host: HostId): Promise<string | null>;
  mutate(host: HostId, bytes: Uint8Array, signal: AbortSignal): Promise<Uint8Array>;
}

/** One run the host reported on; a run already converted is only counted. */
export interface ConvertedRun {
  readonly runId: string;
  readonly status: 'converted' | 'skipped' | 'failed' | 'cannot-convert';
  readonly reason: string;
}

export type RunConversionOutcome =
  | {
      readonly kind: 'converted';
      readonly runs: readonly ConvertedRun[];
      readonly alreadyConverted: number;
      /** Rows the host counted but did not list. */
      readonly unreported: number;
    }
  /** The host's checkout predates the operation. */
  | {readonly kind: 'needs-update'};

export interface RegistryRunConversion {
  convert(host: HostId, project: string, signal: AbortSignal): Promise<RunConversionOutcome>;
}

/**
 * A call that runs out of time answers `complete: false` after converting some runs, so the next
 * call converts the rest. This bounds how many calls one conversion makes.
 */
export const RUN_CONVERSION_CALLS = 20;

const STATUSES: ReadonlyMap<RunConversionStatus, ConvertedRun['status']> = new Map([
  [RunConversionStatus.CONVERTED, 'converted'],
  [RunConversionStatus.SKIPPED, 'skipped'],
  [RunConversionStatus.FAILED, 'failed'],
  [RunConversionStatus.CANNOT_CONVERT, 'cannot-convert'],
]);

export function registryRunConversion(ports: RegistryRunConversionPorts): RegistryRunConversion {
  return {
    async convert(host, project, signal) {
      const transport = mutateTransport(
        ports,
        host,
        requireCheckout(await ports.checkout(host), null),
      );
      const runs: ConvertedRun[] = [];
      let unreported = 0;
      let alreadyConverted = 0;
      let afterRun = '';
      for (let call = 0; call < RUN_CONVERSION_CALLS; call++) {
        if (signal.aborted) throw new Error('run conversion cancelled');
        const response = await convertOnce(transport, project, afterRun, signal);
        if (response === 'needs-update') return {kind: 'needs-update'};
        runs.push(...response.runs.map(convertedRun));
        unreported += response.unreported;
        alreadyConverted += response.alreadyConverted;
        afterRun = response.nextRun;
        if (response.complete) return {kind: 'converted', runs, alreadyConverted, unreported};
      }
      throw new Error(`run conversion did not finish in ${RUN_CONVERSION_CALLS} calls`);
    },
  };
}

function mutateTransport(ports: RegistryRunConversionPorts, host: HostId, checkout: string) {
  return {
    async invoke(id: number, payload: Uint8Array, _totalMs: number, owned: OperationSignal) {
      if (!(owned instanceof AbortSignal)) throw new Error('operation cancelled');
      const policy = Object.values(operationPolicies).find(item => item.id === id);
      if (policy === undefined) throw new Error('unknown mutation');
      return decodeHostMutate(
        policy.name,
        await ports.mutate(host, encodeHostMutate(checkout, policy.name, payload), owned),
      );
    },
  };
}

/** One ConvertSavedRuns call; an UNSUPPORTED failure means the host's checkout predates it. */
async function convertOnce(
  transport: ReturnType<typeof mutateTransport>,
  project: string,
  afterRun: string,
  signal: AbortSignal,
): Promise<ConvertSavedRunsResponse | 'needs-update'> {
  try {
    return await callConvertSavedRuns(
      transport,
      acceptConvertSavedRuns({requestId: newRequestId(), project, afterRun}),
      signal,
    );
  } catch (error) {
    if (error instanceof OperationFailed && error.category === FailureCategory.UNSUPPORTED)
      return 'needs-update';
    throw error;
  }
}

function convertedRun(run: RunConversion): ConvertedRun {
  const status = STATUSES.get(run.status);
  if (status === undefined) throw new Error(`run ${run.runId} has no conversion status`);
  return {runId: run.runId, status, reason: run.reason};
}
