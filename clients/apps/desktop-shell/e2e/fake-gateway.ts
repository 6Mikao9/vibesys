import type {WireFixture} from '../src/testing/gateway-wire.js';

export {type WireFixture, wire} from '../src/testing/gateway-wire.js';

export function installGateway(input: WireFixture): void {
  const calls: string[] = [];
  const streams = new Set<(bytes: Uint8Array) => void>();
  const ended = new Set<(error?: string) => void>();
  const history = [input.tail];
  const chats = new Set<(bytes: Uint8Array) => void>();
  const watchers = new Set<(bytes: Uint8Array) => void>();
  let active = 0;
  Object.assign(window, {
    vibesysGateway: {
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
          receive(new Uint8Array(input.watchRuns));
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
    },
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
        for (const receive of watchers) receive(new Uint8Array(bytes));
      },
      append: (bytes: number[]) => {
        history.push(bytes);
        for (const receive of streams) receive(new Uint8Array(bytes));
      },
    },
  });
}
