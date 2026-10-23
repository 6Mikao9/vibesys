/** Relaunch uses HostCall; the epoch is confirmed by the host writer. */
import {clone, create, equals, fromBinary, toBinary} from '@bufbuild/protobuf';
import {
  HostCallRequestSchema,
  HostCallResponseSchema,
  HostService,
  type Lifecycle,
  LifecycleSchema,
  RelaunchRefusal,
  type RelaunchRequest,
  RelaunchRequestSchema,
  type RelaunchResponse,
  RunLiveness,
  WatchRunsRequestSchema,
  WatchRunsResponseSchema,
} from '@vibesys/proto';
import {refusalMessage} from './refusal.js';

export type {RelaunchRequest, RelaunchResponse} from '@vibesys/proto';
export {RelaunchRefusal, RunLiveness} from '@vibesys/proto';

/** Only these reader observations offer relaunch; the host rechecks admission. */
export interface RelaunchTarget {
  readonly epoch: bigint;
  readonly state: 'parked' | 'ended' | 'interrupted';
}

export function relaunchRequest(
  projectRoot: string,
  runId: string,
  expectedEpoch: bigint,
): RelaunchRequest {
  return create(RelaunchRequestSchema, {projectRoot, runId, expectedEpoch});
}

export function copyRelaunch(request: RelaunchRequest): RelaunchRequest {
  return clone(RelaunchRequestSchema, request);
}

export function hostRelaunchRequest(request: RelaunchRequest): Uint8Array {
  return toBinary(
    HostCallRequestSchema,
    create(HostCallRequestSchema, {method: {case: 'relaunch', value: request}}),
  );
}

export function hostRelaunchResponse(bytes: Uint8Array, expectedEpoch: bigint): RelaunchResponse {
  const response = fromBinary(HostCallResponseSchema, bytes);
  if (response.result.case === 'configurationFailed')
    throw new Error(refusalMessage(response.result.value));
  if (response.result.case !== 'relaunch') throw new Error('Invalid relaunch response');
  const result = response.result.value;
  if (
    (result.outcome.case === 'epoch' && result.outcome.value > expectedEpoch) ||
    (result.outcome.case === 'refusal' &&
      [
        RelaunchRefusal.LOCKED,
        RelaunchRefusal.CHECKOUT_MISSING,
        RelaunchRefusal.EPOCH_CHANGED,
      ].includes(result.outcome.value))
  )
    return result;
  throw new Error('Invalid relaunch outcome');
}

export function relaunchLabel(result: RelaunchResponse | null): string | null {
  if (!result) return null;
  if (result.outcome.case === 'epoch') return `Epoch ${result.outcome.value.toString()}`;
  switch (result.outcome.value) {
    case RelaunchRefusal.LOCKED:
      return 'Refused: locked';
    case RelaunchRefusal.CHECKOUT_MISSING:
      return 'Refused: checkout missing';
    case RelaunchRefusal.EPOCH_CHANGED:
      return 'Refused: epoch changed';
    default:
      return null;
  }
}

export interface RelaunchLiveness {
  readonly epoch: bigint;
  readonly value: RunLiveness;
}

export interface RelaunchObservation {
  readonly epoch: bigint;
  readonly lifecycle?: Lifecycle;
}

export function sameRelaunchObservation(a: RelaunchObservation, b: RelaunchObservation): boolean {
  return (
    a.epoch === b.epoch &&
    (a.lifecycle === undefined || b.lifecycle === undefined
      ? a.lifecycle === b.lifecycle
      : equals(LifecycleSchema, a.lifecycle, b.lifecycle))
  );
}

export const RUN_LIVENESS_METHOD = `/${HostService.typeName}/${HostService.method.watchRuns.name}`;
export function livenessRequest(): Uint8Array {
  return toBinary(WatchRunsRequestSchema, create(WatchRunsRequestSchema));
}
export function livenessResponse(bytes: Uint8Array, runId: string): RelaunchLiveness | null {
  const response = fromBinary(WatchRunsResponseSchema, bytes);
  if (response.runId !== runId) return null;
  return {
    epoch: BigInt(response.head?.epoch ?? 0),
    value: response.removed ? RunLiveness.UNSPECIFIED : response.liveness,
  };
}

export function copyRelaunchObservation(value: RelaunchObservation): RelaunchObservation {
  return {
    epoch: value.epoch,
    ...(value.lifecycle ? {lifecycle: clone(LifecycleSchema, value.lifecycle)} : {}),
  };
}
