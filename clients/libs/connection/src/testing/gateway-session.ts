/** Socket-free implementation of the session role. The peer can send only wire data and trailers. */
import type {Duplex} from 'node:stream';
import {registerAbort, type ScheduleTimeout} from '@vibesys/transport';
import {type GatewaySession, GrpcDecoder, grpcFrame, grpcStatus} from '../shell/grpc.js';
import type {Schedule} from './schedule.js';

export interface GatewayWireCall {
  readonly method: string;
  readonly bytes: Uint8Array;
  readonly signal: AbortSignal;
  message(bytes: Uint8Array): void;
  trailers(status: string, message: string): void;
  close(): void;
}

export class FakeGatewaySession implements GatewaySession {
  readonly #calls = new Set<() => void>();
  constructor(
    private readonly duplex: Duplex,
    private readonly schedule: Schedule,
    private readonly request: (call: GatewayWireCall) => void,
    private readonly timers: ScheduleTimeout,
  ) {}

  close(): void {
    for (const close of [...this.#calls]) close();
    this.duplex.destroy();
  }

  async unary(method: string, bytes: Uint8Array, signal: AbortSignal): Promise<Uint8Array> {
    let response: Uint8Array | undefined;
    await this.stream(
      method,
      bytes,
      signal,
      value => {
        response = value;
      },
      15_000,
    );
    if (response === undefined) throw new Error('unary returned no message');
    return response;
  }

  stream(
    method: string,
    bytes: Uint8Array,
    signal: AbortSignal,
    receive: (bytes: Uint8Array) => void,
    deadlineMs?: number,
  ): Promise<void> {
    return new Promise((resolve, reject) => {
      const lane = {};
      const decoder = new GrpcDecoder();
      let done = false;
      let detach = () => {};
      let cancelDeadline = () => {};
      const close = () => finish(new Error('gRPC connection closed'));
      const finish = (error?: unknown) => {
        if (done) return;
        done = true;
        detach();
        cancelDeadline();
        this.#calls.delete(close);
        if (error !== undefined) reject(error);
        else resolve();
      };
      const consume = (chunk: Uint8Array): void => {
        if (done) return;
        try {
          for (const value of decoder.push(chunk)) receive(value);
        } catch (error) {
          finish(error);
        }
      };
      this.#calls.add(close);
      const registration = registerAbort(signal, () => finish(new Error('cancelled')));
      detach = registration.detach;
      if (registration.aborted) {
        finish(new Error('cancelled'));
        return;
      }
      if (deadlineMs !== undefined)
        cancelDeadline = this.timers(() => finish(new Error('gRPC deadline exceeded')), deadlineMs);
      this.schedule.post(lane, () => {
        if (done) return;
        this.request({
          method,
          bytes: bytes.slice(),
          signal,
          message: payload => {
            const frame = grpcFrame(payload);
            for (let offset = 0; offset < frame.length; ) {
              const size = 1 + this.schedule.random.int(frame.length - offset);
              const chunk = frame.subarray(offset, offset + size);
              offset += size;
              this.schedule.post(lane, () => {
                consume(chunk);
              });
            }
          },
          trailers: (status, message) =>
            this.schedule.post(lane, () => {
              if (done) return;
              try {
                decoder.finish();
                finish(grpcStatus(status, message));
              } catch (error) {
                finish(error);
              }
            }),
          close: () => this.schedule.post(lane, close),
        });
      });
    });
  }
}
