/** Gateway read session effects, sharing the existing skeleton loader and immutable cache role. */
import {create, fromBinary, toBinary} from '@bufbuild/protobuf';
import {clockFrom, type ScheduleTimeout} from '@vibesys/machine';
import {
  GetBlobsRequestSchema,
  GetBlobsResponseSchema,
  HostService,
  TailRequestSchema,
  TailStreamRequestSchema,
  TailStreamResponseSchema,
  WatchRunsRequestSchema,
  type WatchRunsResponse,
  WatchRunsResponseSchema,
} from '@vibesys/proto';
import {type AbortSignalLike, isNotFound} from '@vibesys/transport';
import {createActor, fromCallback, fromPromise} from 'xstate';
import {
  type RunLivenessHint,
  type RunTransport,
  type SkeletonCache,
  type SkeletonLoad,
  type SkeletonLoadFailure,
  startSkeletonLoader,
} from './index.js';

import {isFinalLoad} from './loading.js';
import {
  type ObservationEvent,
  type ObservationOperation,
  type RunReadStatus,
  runObservationMachine,
} from './observation.js';

export type ContentCache = Pick<SkeletonCache, 'getBlob' | 'putBlob'>;
export interface GatewayLogChunk {
  readonly text: string;
  readonly offset: number;
  readonly next: number;
}
export interface GatewayReader {
  /** Resolves `missing` when the host holds no such run; other failures reject. */
  records(
    runId: string,
    fromSeq: bigint,
    signal: AbortSignalLike,
    receive: (bytes: Uint8Array) => void,
  ): Promise<'ended' | 'missing'>;
  watch(
    runId: string,
    receive: (
      load: SkeletonLoad | undefined,
      status: RunReadStatus,
      hint?: RunLivenessHint,
    ) => void,
  ): () => void;
  content(
    runId: string,
    hash: string,
    signal: AbortSignalLike,
  ): Promise<{hash: string; text: string}>;
  log(
    runId: string,
    streamId: string,
    signal: AbortSignalLike,
    receive: (chunk: GatewayLogChunk) => void,
    fromOffset?: bigint,
  ): Promise<void>;
}
const SERVICE = `/${HostService.typeName}/`;

async function content(
  transport: RunTransport,
  cache: ContentCache,
  runId: string,
  hash: string,
  signal: AbortSignalLike,
) {
  let bytes = await cache.getBlob(hash);
  if (signal.aborted) throw new Error('Cancelled');
  if (bytes === undefined) {
    const response = fromBinary(
      GetBlobsResponseSchema,
      await transport.unary(
        `${SERVICE}${HostService.method.getBlobs.name}`,
        toBinary(GetBlobsRequestSchema, create(GetBlobsRequestSchema, {runId, sha256: [hash]})),
        signal,
      ),
    );
    const blob = response.blobs[0];
    if (response.blobs.length !== 1 || !blob || blob.sha256 !== hash || blob.errorCode !== 0)
      throw new Error('Blob unavailable');
    if (signal.aborted) throw new Error('Cancelled');
    await cache.putBlob(hash, blob.data);
    bytes = blob.data;
  }
  return {hash, text: new TextDecoder('utf-8', {fatal: true}).decode(bytes)};
}

async function log(
  transport: RunTransport,
  runId: string,
  streamId: string,
  signal: AbortSignalLike,
  receive: (chunk: GatewayLogChunk) => void,
  fromOffset = 0n,
): Promise<void> {
  if (fromOffset < 0n || fromOffset > BigInt(Number.MAX_SAFE_INTEGER))
    throw new Error('Stream offset exceeds screen precision');
  const decoder = new TextDecoder('utf-8', {fatal: true, ignoreBOM: true});
  const encoder = new TextEncoder();
  let offset = fromOffset;
  let decodedOffset = Number(fromOffset);
  for await (const bytes of transport.stream(
    `${SERVICE}${HostService.method.tailStream.name}`,
    toBinary(
      TailStreamRequestSchema,
      create(TailStreamRequestSchema, {runId, streamId, fromOffset}),
    ),
    signal,
  )) {
    if (signal.aborted) return;
    const response = fromBinary(TailStreamResponseSchema, bytes);
    if (
      response.nextOffset !== offset + BigInt(response.data.length) ||
      response.nextOffset > BigInt(Number.MAX_SAFE_INTEGER)
    )
      throw new Error('Stream offset mismatch');
    const text = decoder.decode(response.data, {stream: true});
    const next = decodedOffset + encoder.encode(text).length;
    if (text !== '') receive({text, offset: decodedOffset, next});
    decodedOffset = next;
    offset = response.nextOffset;
  }
  if (!signal.aborted) decoder.decode();
}

export function gatewayReader(
  transport: RunTransport,
  cache: ContentCache,
  schedule: ScheduleTimeout,
): GatewayReader {
  return {
    async records(runId, fromSeq, signal, receive) {
      try {
        for await (const bytes of transport.stream(
          `${SERVICE}${HostService.method.tail.name}`,
          toBinary(TailRequestSchema, create(TailRequestSchema, {runId, fromSeq})),
          signal,
        )) {
          if (signal.aborted) return 'ended';
          receive(bytes);
        }
      } catch (error) {
        if (isNotFound(error)) return 'missing';
        throw error;
      }
      return 'ended';
    },
    watch(runId, receive) {
      const actor = createActor(
        runObservationMachine.provide({
          actors: {
            history: fromCallback<ObservationEvent, ObservationOperation>(({input}) => {
              const loader = startSkeletonLoader(transport, schedule, input);
              const subscription = loader.subscribe(snapshot =>
                input.receive({
                  type: 'read',
                  load: snapshot.context.load,
                  status: readStatus(snapshot.context.error),
                }),
              );
              return () => {
                subscription.unsubscribe();
                loader.stop();
              };
            }),
            watch: fromPromise<void, ObservationOperation>(async ({input, signal}) => {
              for await (const bytes of transport.stream(
                `${SERVICE}${HostService.method.watchRuns.name}`,
                toBinary(WatchRunsRequestSchema, create(WatchRunsRequestSchema)),
                signal,
              )) {
                if (signal.aborted) return;
                const response = fromBinary(WatchRunsResponseSchema, bytes);
                input.receive({type: 'ready'});
                if (response.runId !== input.runId) continue;
                input.receive({
                  type: 'hint',
                  hint: response.removed
                    ? undefined
                    : {
                        epoch: observedEpoch(response),
                        value: response.liveness,
                        head: response.head,
                      },
                });
              }
            }),
          },
        }),
        {input: {runId, timeoutMs: 30000}, clock: clockFrom(schedule)},
      ).start();
      const subscription = actor.subscribe(({context}) =>
        receive(
          context.load,
          context.status === 'ok' && context.watchFailed && !isFinalLoad(context.load)
            ? 'failed'
            : context.status,
          context.hint,
        ),
      );
      return () => {
        subscription.unsubscribe();
        actor.stop();
      };
    },
    content: (runId, hash, signal) => content(transport, cache, runId, hash, signal),
    log: (runId, streamId, signal, receive, fromOffset) =>
      log(transport, runId, streamId, signal, receive, fromOffset),
  };
}

function readStatus(error: SkeletonLoadFailure | undefined): RunReadStatus {
  if (error === undefined) return 'ok';
  return error.kind === 'not-found' ? 'missing' : 'failed';
}

function observedEpoch(response: WatchRunsResponse): number {
  // Phase 1 heads omit the canonical epoch (proto3 decodes it as zero).
  // Preserve their process epoch for fencing; liveness still comes from the gateway's lock probe.
  return response.head?.epoch || response.head?.process?.epoch || 0;
}
