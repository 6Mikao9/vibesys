/**
 * A gateway's refusal keeps its gRPC status across every hop to the page: the peer's trailers, the
 * main process's call and handler, the channel port, the byte-only preload and the page's client.
 * The real gateway connection, handler, preload factory and page client run; only Electron's own
 * boundary is modelled: the `ipcMain` registry and the channel ports (Node's real `MessageChannel`).
 * Only protobuf bytes cross the preload, so the status travels inside the generated end message.
 */
import {describe, expect, test} from 'bun:test';
import {createRequire} from 'node:module';
import {create, type DescMessage, type MessageInitShape, toBinary} from '@bufbuild/protobuf';
import {GatewayCalls, type GatewayDialer} from '@vibesys/connection';
import {FakeGateway} from '@vibesys/connection/testing';
import {GATEWAY_CHANNEL, GatewayError, gatewayBridge} from '@vibesys/ipc';
import {
  GetBlobsRequestSchema,
  HostService,
  SkeletonRequestSchema,
  TailRequestSchema,
  TailStreamRequestSchema,
  WatchRunsRequestSchema,
} from '@vibesys/proto';
import {SettingsStore} from '@vibesys/settings';
import {FakeSettingsFilesystem} from '@vibesys/settings/testing';
import {FakeClock} from '@vibesys/transport/testing';
import type {IpcMain} from 'electron';
import {installGatewayIpc} from '../main/gateway-ipc.js';

const {createGatewayPreload} = createRequire(import.meta.url)(
  './welcome.cjs',
) as typeof import('./welcome.cjs');

function encode<T extends DescMessage>(schema: T, init: MessageInitShape<T>): Uint8Array {
  return toBinary(schema, create(schema, init));
}
const method = (name: keyof typeof HostService.method): string =>
  `/${HostService.typeName}/${HostService.method[name].name}`;
/** Every gateway call the page makes: its unary reads and its streams (history, logs, liveness). */
const UNARY = [
  [method('skeleton'), encode(SkeletonRequestSchema, {runId: 'a'})],
  [method('getBlobs'), encode(GetBlobsRequestSchema, {runId: 'a'})],
] as const;
const STREAMS = [
  [method('tail'), encode(TailRequestSchema, {runId: 'a'})],
  [method('tailStream'), encode(TailStreamRequestSchema, {runId: 'a', streamId: 'out'})],
  [method('watchRuns'), encode(WatchRunsRequestSchema, {})],
] as const;
/** gRPC status codes 1 to 16: every code but OK, which is not a refusal. */
const REFUSALS = Array.from({length: 16}, (_, index) => index + 1);

type Listener = Parameters<IpcMain['on']>[1];

/** A channel end as the main process holds it (`MessagePortMain`): `on`, `start`, `postMessage`, `close`. */
function mainPort(port: MessagePort) {
  return {
    on: (event: 'message' | 'close', listener: (event: unknown) => void) =>
      port.addEventListener(event, listener),
    start: () => port.start(),
    postMessage: (value: unknown) => port.postMessage(value),
    close: () => port.close(),
  };
}

/** `ipcMain`, with the sender the handler authorizes and the port the preload sent. */
class FakeIpcMain {
  readonly #listeners = new Map<string, Listener>();
  readonly on = ((channel: string, listener: Listener) => {
    this.#listeners.set(channel, listener);
  }) as unknown as IpcMain['on'];

  post(channel: string, value: unknown, ports: readonly MessagePort[]): void {
    const main = ports.map(mainPort);
    const frame = {processId: 1, routingId: 1, url: 'app://vibesys/index.html'};
    const sender = {
      id: 1,
      mainFrame: frame,
      once: () => undefined,
      on: () => undefined,
      removeListener: () => undefined,
    };
    const listener = this.#listeners.get(channel);
    if (listener === undefined) throw new Error(`no handler on ${channel}`);
    listener({sender, senderFrame: frame, ports: main} as never, value);
  }
}

function pageBridge(peer: GatewayDialer) {
  const clock = new FakeClock();
  const calls = new GatewayCalls(() => peer, clock.schedule);
  const ipc = new FakeIpcMain();
  const settings = new SettingsStore({
    settingsPath: '/settings/hosts.json',
    recentPath: '/settings/recent.json',
    preferencesPath: '/settings/preferences.json',
    log: () => undefined,
    filesystem: new FakeSettingsFilesystem(),
  });
  installGatewayIpc(ipc as unknown as IpcMain, {calls, settings, admits: () => true});
  const preload = createGatewayPreload({
    channel: () => {
      const {port1, port2} = new MessageChannel();
      return {
        listen: (receive, failed) => {
          port1.onmessage = event => receive(event.data);
          port1.onmessageerror = failed;
        },
        send: request => ipc.post(GATEWAY_CHANNEL, request, [port2]),
        close: () => port1.close(),
      };
    },
  });
  return {bridge: gatewayBridge(preload), calls};
}

describe('a refusal keeps its gRPC status from the gateway to the page', () => {
  test('every code, on every unary call', async () => {
    const peer = new FakeGateway();
    const {bridge, calls} = pageBridge(peer);
    try {
      for (const [path, request] of UNARY)
        for (const code of REFUSALS) {
          peer.refuse(path, code);
          const error = await bridge.unary('local', path, request).result.then(
            () => undefined,
            (reason: unknown) => reason,
          );
          expect(error).toBeInstanceOf(GatewayError);
          expect((error as GatewayError).status).toBe(code);
        }
    } finally {
      calls.close();
      peer.close();
    }
  });

  test('every code, on every stream', async () => {
    const peer = new FakeGateway();
    const {bridge, calls} = pageBridge(peer);
    try {
      for (const [path, request] of STREAMS)
        for (const code of REFUSALS) {
          peer.refuse(path, code);
          const status = await new Promise<number | undefined>(resolve => {
            bridge.stream(
              'local',
              path,
              request,
              () => undefined,
              (_error, reported) => resolve(reported),
            );
          });
          expect(status).toBe(code);
        }
    } finally {
      calls.close();
      peer.close();
    }
  });
});
