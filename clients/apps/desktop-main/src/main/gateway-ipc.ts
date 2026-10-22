/** Generic IPC composition. Authorization precedes provisioning or dialing. */
import {arch, platform} from 'node:os';
import {fromBinary, toBinary} from '@bufbuild/protobuf';
import {
  type ConnectionManager,
  GATEWAY_DIRECTORY,
  GatewayCalls,
  type GatewayDialer,
  gatewayArtifact,
  gatewayProcess,
  gatewayTarget,
  localGateway,
  provisionGateway,
  READ_METHOD,
  SshLink,
} from '@vibesys/connection';
import {type HostKey, hostFromKey} from '@vibesys/hosts';
import {GATEWAY_CHANNEL, gatewayEnd, gatewayReply, parseGatewayRequest} from '@vibesys/ipc';
import {ReadRequestSchema} from '@vibesys/proto';
import {configuredHostCall, HOST_CALL_METHOD} from '@vibesys/runs/intents';
import {type SettingsStore, settingsFor} from '@vibesys/settings';
import type {ScheduleTimeout} from '@vibesys/transport';
import type {IpcMain} from 'electron';
import {rendererGeneration, type WelcomeInvokeEvent} from './welcome-ipc.js';

export function createGatewayCalls(
  manager: ConnectionManager,
  resources: string,
  schedule: ScheduleTimeout,
): GatewayCalls {
  const calls = new GatewayCalls(key => gatewayFor(key, manager, resources, schedule), schedule);
  manager.onDisposing(() => calls.close());
  return calls;
}

export function installGatewayIpc(
  ipc: IpcMain,
  {
    calls,
    settings,
    admits,
  }: {
    calls: GatewayCalls;
    settings: SettingsStore;
    admits: (event: WelcomeInvokeEvent) => boolean;
  },
): void {
  ipc.on(GATEWAY_CHANNEL, (event, value: unknown) => {
    const port = event.ports[0];
    if (port === undefined) return;
    const abort = new AbortController();
    let done = false;
    const finish = (error?: unknown): void => {
      if (done) return;
      done = true;
      abort.abort();
      event.sender.removeListener('destroyed', cancelled);
      event.sender.removeListener('did-start-navigation', navigated);
      port.postMessage(gatewayEnd(error === undefined ? undefined : String(error)));
      port.close();
    };
    const cancelled = (): void => finish(new Error('gateway caller closed'));
    const navigated = (
      _event: unknown,
      _url: string,
      inPage: boolean,
      mainFrame: boolean,
    ): void => {
      if (!inPage && mainFrame) cancelled();
    };
    port.on('message', cancelled);
    port.on('close', cancelled);
    port.start();
    event.sender.once('destroyed', cancelled);
    event.sender.on('did-start-navigation', navigated);
    void (async () => {
      authorize(event, admits);
      const request = parseGatewayRequest(value);
      const receive = (bytes: Uint8Array): void => {
        if (!done) port.postMessage(gatewayReply(bytes));
      };
      if (request.kind === 'unary') {
        let bytes = request.bytes;
        if (request.method === HOST_CALL_METHOD || request.method === READ_METHOD) {
          const {settings: file} = await settings.hosts();
          const checkout = settingsFor(file, hostFromKey(request.host))?.checkout ?? null;
          if (request.method === HOST_CALL_METHOD) bytes = configuredHostCall(bytes, checkout);
          else {
            if (checkout === null) throw new Error('No checkout configured for this host');
            const read = fromBinary(ReadRequestSchema, bytes);
            read.checkout = checkout;
            bytes = toBinary(ReadRequestSchema, read);
          }
        }
        receive(await calls.unary(request.host, request.method, bytes, abort.signal));
      } else await calls.stream(request.host, request.method, request.bytes, abort.signal, receive);
    })().then(() => finish(), finish);
  });
}

function gatewayFor(
  key: HostKey,
  manager: ConnectionManager,
  resources: string,
  schedule: ScheduleTimeout,
): GatewayDialer {
  const host = hostFromKey(key);
  return {
    dial: async (signal, force) => {
      if (host.kind === 'local')
        return localGateway(resources, gatewayTarget(`${platform()} ${arch()}`), signal, schedule);
      const lease = await manager.leaseLink(host);
      try {
        if (!(lease.link instanceof SshLink)) throw new Error('host has no SSH command link');
        await lease.link.establish(false, signal);
        await provisionGateway(
          lease.link,
          target => gatewayArtifact(resources, target),
          force,
          signal,
          schedule,
        );
        if (signal.aborted) throw new Error('gateway dial cancelled');
        const duplex = gatewayProcess(
          lease.link.spawnCommand(`exec "$HOME/${GATEWAY_DIRECTORY}/vibesysd" connect`),
          schedule,
        );
        duplex.once('close', () => {
          void lease.release();
        });
        return duplex;
      } catch (error) {
        await lease.release();
        throw error;
      }
    },
  };
}

function authorize(
  event: import('electron').IpcMainEvent,
  admits: (event: WelcomeInvokeEvent) => boolean,
): void {
  if (
    event.senderFrame !== event.sender.mainFrame ||
    !admits({
      senderId: event.sender.id,
      senderUrl: event.senderFrame?.url ?? null,
      rendererGeneration: rendererGeneration(event.senderFrame),
    })
  )
    throw new Error('not an app-owned shell document');
}
