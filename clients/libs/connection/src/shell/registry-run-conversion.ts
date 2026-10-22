/** Convert a project's stopped pre-transport runs on a host so the run list can read them. */
import type {HostId} from '@vibesys/hosts';
import {
  acceptConvertSavedRuns,
  callConvertSavedRuns,
  decodeHostMutate,
  encodeHostMutate,
  FailureCategory,
  OperationFailed,
  type OperationSignal,
  operationPolicies,
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
  readonly status: 'converted' | 'skipped' | 'failed';
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
]);

export function registryRunConversion(ports: RegistryRunConversionPorts): RegistryRunConversion {
  return {
    async convert(host, project, signal) {
      const checkout = requireCheckout(await ports.checkout(host), null);
      const transport = {
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
      const runs: ConvertedRun[] = [];
      let alreadyConverted = 0;
      let unreported = 0;
      for (let call = 0; call < RUN_CONVERSION_CALLS; call++) {
        let response: Awaited<ReturnType<typeof callConvertSavedRuns>>;
        try {
          response = await callConvertSavedRuns(
            transport,
            acceptConvertSavedRuns({requestId: newRequestId(), project}),
            signal,
          );
        } catch (error) {
          if (error instanceof OperationFailed && error.category === FailureCategory.UNSUPPORTED)
            return {kind: 'needs-update'};
          throw error;
        }
        for (const run of response.runs) {
          const status = STATUSES.get(run.status);
          if (status === undefined) throw new Error(`run ${run.runId} has no conversion status`);
          runs.push({runId: run.runId, status, reason: run.reason});
        }
        alreadyConverted = response.alreadyConverted;
        unreported += response.unreported;
        if (response.complete) return {kind: 'converted', runs, alreadyConverted, unreported};
      }
      throw new Error(`run conversion did not finish in ${RUN_CONVERSION_CALLS} calls`);
    },
  };
}
