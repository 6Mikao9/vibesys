/** Browser fixture for the same protobuf port ABI the Electron preload exposes. */

import {fromBinary, toBinary} from '@bufbuild/protobuf';
import {
  type GatewayBridge,
  type GatewayPortBridge,
  gatewayEnd,
  gatewayReply,
  parseGatewayRequest,
} from '@vibesys/ipc';
import {
  RecordSchema,
  SkeletonResponseSchema,
  TailResponseSchema,
  WatchRunsResponseSchema,
} from '@vibesys/proto';
import {foldSkeleton, observeRun, seedSkeleton} from '@vibesys/run-skeleton';
import type {WireFixture} from './fake-gateway.js';

export function installGateway(input: WireFixture): void {
  const calls: string[] = [];
  const streams = new Set<(bytes: Uint8Array) => void>();
  const ended = new Set<(error?: string) => void>();
  const history = [input.tail];
  const observations = fixtureObservations(input);
  const chats = new Set<(bytes: Uint8Array) => void>();
  const watchers = new Set<(bytes: Uint8Array) => void>();
  let active = 0;
  const bridge: GatewayBridge = {
    unary: (host: string, method: string, request: Uint8Array) => {
      calls.push(method);
      if (method === input.methods.hostCall) {
        const peer = window as unknown as {
          postIntent(host: string, bytes: number[]): Promise<number[]>;
        };
        return {
          result: peer.postIntent(host, [...request]).then(bytes => new Uint8Array(bytes)),
          cancel: () => undefined,
        };
      }
      const bytes =
        method === input.methods.skeleton
          ? input.skeleton
          : method === input.methods.blobs
            ? input.blobs[[...request].join(',')]
            : undefined;
      if (!bytes) throw new Error('Unknown unary');
      return {result: Promise.resolve(new Uint8Array(bytes)), cancel: () => undefined};
    },
    stream: (
      _host: string,
      method: string,
      _bytes: Uint8Array,
      receive: (bytes: Uint8Array) => void,
      finish: (error?: string) => void,
    ) => {
      ended.add(finish);
      calls.push(method);
      active += 1;
      let closed = false;
      if (method === input.methods.tail) {
        if (
          [..._bytes].slice(-input.chatRequest.length).join(',') === input.chatRequest.join(',')
        ) {
          chats.add(receive);
          receive(new Uint8Array(input.chat));
        } else {
          streams.add(receive);
          for (const bytes of history) receive(new Uint8Array(bytes));
        }
      } else if (method === input.methods.watchRuns) {
        watchers.add(receive);
        receive(observations.decode(new Uint8Array(input.watchRuns)));
      } else if (method === input.methods.log) receive(new Uint8Array(input.log));
      else throw new Error('Unknown stream');
      return {
        cancel: () => {
          if (!closed) {
            closed = true;
            active -= 1;
            streams.delete(receive);
            ended.delete(finish);
            chats.delete(receive);
            watchers.delete(receive);
          }
        },
      };
    },
  };

  Object.assign(window, {
    vibesysGateway: preloadOver(bridge),
    fakeGateway: {
      appendChat: (bytes: number[]) => {
        for (const receive of chats) receive(new Uint8Array(bytes));
      },
      disconnect: () => {
        for (const finish of [...ended]) finish('SSH connection closed');
      },
      calls: () => calls,
      active: () => active,
      observe: (bytes: number[]) => {
        for (const receive of watchers) receive(observations.decode(new Uint8Array(bytes)));
      },
      append: (bytes: number[]) => {
        observations.append(bytes);
        history.push(bytes);
        for (const receive of streams) receive(new Uint8Array(bytes));
      },
    },
  });
}

function preloadOver(bridge: GatewayBridge): GatewayPortBridge {
  return {
    open(encoded, receive, failed) {
      const request = parseGatewayRequest(encoded);
      if (request.kind === 'stream') {
        return bridge.stream(
          request.host,
          request.method,
          request.bytes,
          bytes => receive(gatewayReply(bytes)),
          error => receive(gatewayEnd(error)),
        );
      }
      const call = bridge.unary(request.host, request.method, request.bytes);
      let cancelled = false;
      void call.result.then(
        bytes => {
          if (cancelled) return;
          receive(gatewayReply(bytes));
          if (!cancelled) receive(gatewayEnd());
        },
        error => {
          if (!cancelled) failed(String(error));
        },
      );
      return {
        cancel() {
          cancelled = true;
          call.cancel();
        },
      };
    },
  };
}

function fixtureObservations(input: WireFixture) {
  const records = [
    ...fromBinary(SkeletonResponseSchema, new Uint8Array(input.skeleton)).records,
    ...fromBinary(TailResponseSchema, new Uint8Array(input.tail)).records,
  ].map(bytes => fromBinary(RecordSchema, bytes));
  // Match the gateway's retained-head decoder using canonical log facts.
  // A process identity is never a source of epoch authority, including in a Fake.
  const decode = (bytes: Uint8Array): Uint8Array => {
    const response = fromBinary(WatchRunsResponseSchema, bytes);
    const head = response.head;
    if (head && head.epoch === 0) {
      const prefix = records.filter(
        record =>
          head.committedSeq === undefined ||
          (head.committedSeq > 0n && record.seq <= head.committedSeq),
      );
      head.epoch = observeRun(foldSkeleton(seedSkeleton(), prefix)).epoch;
    }
    return toBinary(WatchRunsResponseSchema, response);
  };
  return {
    decode,
    append(bytes: number[]): void {
      records.push(
        ...fromBinary(TailResponseSchema, new Uint8Array(bytes)).records.map(record =>
          fromBinary(RecordSchema, record),
        ),
      );
    },
  };
}
