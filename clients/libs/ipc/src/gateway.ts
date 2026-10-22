/** Generic protobuf bridge. The caller owns every call and cancels it on leaving its screen. */
import {type HostKey, hostFromKey} from '@vibesys/hosts';
import {GrpcCode, GrpcError} from '@vibesys/transport';

export const GATEWAY_CHANNEL = 'vibesys:gateway';
export interface GatewayCall<T> {
  readonly result: Promise<T>;
  cancel(): void;
}
export interface GatewayBridge {
  unary(host: HostKey, method: string, bytes: Uint8Array): GatewayCall<Uint8Array>;
  stream(
    host: HostKey,
    method: string,
    bytes: Uint8Array,
    receive: (bytes: Uint8Array) => void,
    /** `code` is the gateway's gRPC status when the host answered with one. */
    ended: (error?: string, code?: number) => void,
  ): {cancel(): void};
}
/**
 * A unary reply as it crosses the context bridge. The bridge keeps only an Error's message, so a
 * failure travels as data and `gatewayBridge` turns it back into a typed error in the renderer.
 */
export type GatewayUnaryReply =
  | {readonly bytes: Uint8Array}
  | {readonly error: string; readonly code?: number};
/** What the preload exposes; the renderer reads it only through `gatewayBridge`. */
export interface GatewayPreloadBridge {
  unary(host: HostKey, method: string, bytes: Uint8Array): GatewayCall<GatewayUnaryReply>;
  stream: GatewayBridge['stream'];
}
export interface GatewayRequest {
  readonly kind: 'unary' | 'stream';
  readonly host: HostKey;
  readonly method: string;
  readonly bytes: Uint8Array;
}
export type GatewayReply =
  | {readonly bytes: Uint8Array}
  | {readonly end: true; readonly error?: string; readonly code?: number};

export function parseGatewayRequest(value: unknown): GatewayRequest {
  if (value === null || typeof value !== 'object')
    throw new Error('gateway request must be an object');
  const record = value as Record<string, unknown>;
  for (const key of Object.keys(record)) {
    if (!['kind', 'host', 'method', 'bytes'].includes(key))
      throw new Error(`unknown gateway key: ${key}`);
  }
  const {kind, host, method, bytes} = record;
  hostFromKey(host);
  if (kind !== 'unary' && kind !== 'stream')
    throw new Error('gateway kind must be unary or stream');
  if (typeof method !== 'string' || !/^\/[A-Za-z][\w.]*\/[A-Za-z]\w*$/.test(method))
    throw new Error('invalid gateway method');
  if (!(bytes instanceof Uint8Array) || bytes.byteLength > 16 * 1024 * 1024)
    throw new Error('gateway bytes must be at most 16 MiB');
  return {kind, host: host as HostKey, method, bytes};
}

/** The renderer's typed failure for an ended call: the gRPC status survives the bridge. */
export function gatewayFailure(error: string, code?: number): Error {
  return code === undefined ? new Error(error) : new GrpcError(code, error);
}

/** The host answered that it holds no such run or stream. */
export function isNotFoundCode(code: number | undefined): boolean {
  return code === GrpcCode.NOT_FOUND;
}

/** The renderer's bridge over the preload's: a failed unary call rejects with `gatewayFailure`. */
export function gatewayBridge(preload: GatewayPreloadBridge): GatewayBridge {
  return {
    unary(host, method, bytes) {
      const call = preload.unary(host, method, bytes);
      return {
        result: call.result.then(reply => {
          if ('bytes' in reply) return reply.bytes;
          throw gatewayFailure(reply.error, reply.code);
        }),
        cancel: () => call.cancel(),
      };
    },
    stream: (host, method, bytes, receive, ended) =>
      preload.stream(host, method, bytes, receive, ended),
  };
}
