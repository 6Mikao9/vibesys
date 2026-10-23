/**
 * Cross-process composition with only message delivery and the socket session replaced. A
 * helper for tests, not a test file: the test runner collects only `*.test.ts`.
 */
import {EventEmitter} from 'node:events';
import {createRequire} from 'node:module';
import {PassThrough} from 'node:stream';
import {create, toBinary} from '@bufbuild/protobuf';
import {GatewayCalls, type GatewaySessionFactory, HELLO_METHOD} from '@vibesys/connection';
import {FakeGatewaySession, type GatewayWireCall, Schedule} from '@vibesys/connection/testing';
import {type GatewayRequest, gatewayTransport} from '@vibesys/ipc';
import {FakeGatewayPort} from '@vibesys/ipc/testing';
import {API_SCHEMA_HASH, HelloResponseSchema} from '@vibesys/proto';
import {SettingsStore} from '@vibesys/settings';
import {FakeSettingsFilesystem} from '@vibesys/settings/testing';
import {FakeClock} from '@vibesys/transport/testing';
import type {IpcMainEvent} from 'electron';
import {installGatewayIpc} from '../main/gateway-ipc.js';

const createPreload = createRequire(import.meta.url)(
  '../preload/welcome.cjs',
) as typeof import('../preload/welcome.cjs');

export function gatewayWorld(seed: number, request: (call: GatewayWireCall) => void) {
  const clock = new FakeClock();
  const schedule = new Schedule(seed, clock);
  const session: GatewaySessionFactory = (duplex, _timers, lost) =>
    new FakeGatewaySession(
      duplex,
      schedule,
      call => {
        if (call.method === HELLO_METHOD) {
          call.message(
            toBinary(
              HelloResponseSchema,
              create(HelloResponseSchema, {
                apiSchemaHash: API_SCHEMA_HASH,
                gatewayVersion: 'test',
              }),
            ),
          );
          call.trailers('0', '');
        } else request(call);
      },
      clock.schedule,
      lost,
    );
  const calls = new GatewayCalls(
    async () => true,
    () => ({dial: async () => new PassThrough(), session}),
    clock.schedule,
  );
  let listener: (event: IpcMainEvent, value: unknown) => void = () => {
    throw new Error('IPC not installed');
  };
  const settings = new SettingsStore({
    settingsPath: '/settings/hosts.json',
    recentPath: '/settings/recent.json',
    preferencesPath: '/settings/preferences.json',
    filesystem: new FakeSettingsFilesystem(),
    log: () => {},
  });
  installGatewayIpc(
    {
      on: (_channel, receive) => {
        listener = receive;
      },
    },
    {calls, settings, admits: () => true},
  );
  const sender = Object.assign(new EventEmitter(), {
    id: 1,
    mainFrame: {url: 'app://vibesys/index.html', processId: 1, routingId: 1},
  });
  const ports: FakeGatewayPort[] = [];
  const preload = createPreload({
    platform: 'test',
    invoke: async () => ({ok: true, value: true}),
    on: () => () => {},
    connectRun: () => {},
    openGatewayPort: (value: GatewayRequest) => {
      const {page, main} = FakeGatewayPort.pair(
        (lane, run) => schedule.post(lane, run),
        structuredClone,
      );
      ports.push(page);
      // test-isolation: Electron owns a much larger event, but gateway IPC reads only these fields.
      listener(
        {ports: [main], sender, senderFrame: sender.mainFrame} as unknown as IpcMainEvent,
        value,
      );
      return page;
    },
  });
  const bridge = preload.gateway;
  return {
    schedule,
    clock,
    bridge,
    sender,
    ports,
    preload,
    settings,
    transport: gatewayTransport(bridge, 'local'),
    close: () => {
      calls.close();
      for (const port of ports) port.close();
    },
  };
}
