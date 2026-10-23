import type {HostKey} from '@vibesys/hosts';
import type {GatewayBridge} from '@vibesys/ipc';
import {clockFrom, type ScheduleTimeout} from '@vibesys/machine';
import {CHAT_TAIL_METHOD, chatTailRequest, chatTailResponse} from '@vibesys/runs/chat';
import {createActor, fromCallback} from 'xstate';
import {type SteerWatchEvent, steerWatchMachine} from '../../state/index.js';

export interface SteerPorts {
  readonly bridge: GatewayBridge;
  readonly host: HostKey;
  readonly runId: string;
  readonly schedule: ScheduleTimeout;
}

export function steerActor(ports: SteerPorts) {
  return createActor(
    steerWatchMachine.provide({
      actors: {
        watch: fromCallback<SteerWatchEvent, bigint>(({input: fromSeq, sendBack}) => {
          const call = ports.bridge.stream(
            ports.host,
            CHAT_TAIL_METHOD,
            chatTailRequest(ports.runId, '', fromSeq),
            bytes => {
              try {
                sendBack({type: 'records', records: chatTailResponse(bytes)});
              } catch {
                sendBack({type: 'disconnected'});
              }
            },
            () => sendBack({type: 'disconnected'}),
          );
          return () => call.cancel();
        }),
      },
    }),
    {clock: clockFrom(ports.schedule)},
  );
}
