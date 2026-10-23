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
  HostCallRequestSchema,
  RecordSchema,
  SkeletonResponseSchema,
  TailRequestSchema,
  TailResponseSchema,
  TailStreamRequestSchema,
  WatchRunsResponseSchema,
} from '@vibesys/proto';
import {foldSkeleton, observeRun, seedSkeleton} from '@vibesys/run-skeleton';
import {ChatPeer} from './chat-peer.js';
import type {WireFixture} from './fake-gateway.js';

type Receive = (bytes: Uint8Array) => void;

const settled = (bytes: Uint8Array) => ({result: Promise.resolve(bytes), cancel: () => undefined});

/** The page-side gateway: serves the fixture's frames and records what the client asked of it. */
class FixtureGateway {
  readonly calls: string[] = [];
  readonly streams = new Set<Receive>();
  readonly ended = new Set<(error?: string) => void>();
  readonly watchers = new Set<Receive>();
  readonly history: number[][];
  readonly observations: ReturnType<typeof fixtureObservations>;
  readonly chat: ChatPeer;
  active = 0;

  constructor(readonly input: WireFixture) {
    this.history = [input.tail];
    this.observations = fixtureObservations(input);
    this.chat = new ChatPeer(input.chat);
  }

  unary = (host: string, method: string, request: Uint8Array) => {
    this.calls.push(method);
    const local = this.chat.unary(method, request);
    if (local) return settled(local);
    if (method === this.input.methods.hostCall) return this.hostCall(host, request);
    const bytes =
      method === this.input.methods.skeleton
        ? this.input.skeleton
        : method === this.input.methods.blobs
          ? this.input.blobs[[...request].join(',')]
          : undefined;
    if (!bytes) throw new Error('Unknown unary');
    return settled(new Uint8Array(bytes));
  };

  stream = (
    _host: string,
    method: string,
    bytes: Uint8Array,
    receive: Receive,
    finish: (error?: string) => void,
  ) => {
    this.ended.add(finish);
    this.calls.push(method);
    this.active += 1;
    let closed = false;
    const cancelChat = this.subscribe(method, bytes, receive);
    return {
      cancel: () => {
        if (closed) return;
        closed = true;
        this.active -= 1;
        this.streams.delete(receive);
        this.ended.delete(finish);
        cancelChat();
        this.watchers.delete(receive);
      },
    };
  };

  private hostCall(host: string, request: Uint8Array) {
    const peer = window as unknown as {
      postIntent(host: string, bytes: number[]): Promise<number[]>;
    };
    const operation = fromBinary(HostCallRequestSchema, request).method;
    if (!peer.postIntent && operation.case === 'chatMessage')
      return settled(
        this.chat.message(
          operation.value.sessionId,
          operation.value.requestId,
          operation.value.text,
        ),
      );
    return {
      result: peer.postIntent(host, [...request]).then(bytes => new Uint8Array(bytes)),
      cancel: () => undefined,
    };
  }

  /** Subscribe one stream and return what releases it from the chat peer. */
  private subscribe(method: string, bytes: Uint8Array, receive: Receive): () => void {
    const {methods} = this.input;
    if (method === methods.tail) {
      const tail = fromBinary(TailRequestSchema, bytes);
      if (tail.sessionId !== '') return this.chat.watch(tail.sessionId, tail.fromSeq, receive);
      this.streams.add(receive);
      for (const entry of this.history) receive(new Uint8Array(entry));
    } else if (method === methods.watchRuns) {
      this.watchers.add(receive);
      receive(this.observations.decode(new Uint8Array(this.input.watchRuns)));
    } else if (method === methods.log)
      receive(
        new Uint8Array(
          this.input.streams?.[fromBinary(TailStreamRequestSchema, bytes).streamId] ??
            this.input.log,
        ),
      );
    else throw new Error('Unknown stream');
    return () => undefined;
  }

  controls() {
    return {
      appendChat: (bytes: number[]) => {
        this.chat.append(bytes);
      },
      sendChatFrame: (bytes: number[]) => this.chat.publish(bytes),
      seedChat: (id: string, bytes: number[]) => this.chat.append(bytes, id),
      disconnect: () => {
        for (const finish of [...this.ended]) finish('SSH connection closed');
      },
      calls: () => this.calls,
      active: () => this.active,
      observe: (bytes: number[]) => {
        for (const receive of this.watchers)
          receive(this.observations.decode(new Uint8Array(bytes)));
      },
      append: (bytes: number[]) => {
        this.observations.append(bytes);
        this.history.push(bytes);
        for (const receive of this.streams) receive(new Uint8Array(bytes));
      },
    };
  }
}

export function installGateway(input: WireFixture): void {
  const gateway = new FixtureGateway(input);
  Object.assign(window, {
    vibesysGateway: preloadOver({unary: gateway.unary, stream: gateway.stream}),
    fakeGateway: gateway.controls(),
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
