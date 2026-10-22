/** The feature supplies session operations to its machine; the session owns wire I/O. */
import {clockFrom, type ScheduleTimeout} from '@vibesys/machine';
import type {GatewayReader} from '@vibesys/run-skeleton/reader';
import {tailRecordRows} from '@vibesys/run-state/records';
import {createActor, fromCallback, fromPromise} from 'xstate';
import {
  type GatewayEvent,
  type GatewayInput,
  type GatewayLogInput,
  type GatewayRecordsInput,
  gatewayRunMachine,
} from '../../state/index.js';

export interface GatewayRunPorts {
  readonly reader: GatewayReader;
  watchSetting(listener: () => void): () => void;
  setting(enabled: boolean | undefined): Promise<boolean>;
}
export function startGatewayRun(
  ports: GatewayRunPorts,
  schedule: ScheduleTimeout,
  input: GatewayInput,
) {
  return createActor(
    gatewayRunMachine.provide({
      actors: {
        watchSetting: fromCallback(({sendBack}) =>
          ports.watchSetting(() => sendBack({type: 'setting-changed'})),
        ),
        setting: fromPromise(({input}) => ports.setting(input)),
        loader: fromCallback<GatewayEvent, {runId: string}>(({input, sendBack}) =>
          ports.reader.watch(input.runId, (load, status, hint) =>
            sendBack({type: 'loaded', load, status, ...(hint ? {hint} : {})}),
          ),
        ),
        content: fromPromise(({input, signal}) =>
          ports.reader.content(input.runId, input.hash, signal),
        ),
        log: fromPromise<void, GatewayLogInput>(async ({input, signal}) => {
          try {
            await ports.reader.log(input.runId, input.streamId, signal, chunk =>
              input.receive({type: 'log', streamId: input.streamId, ...chunk}),
            );
          } catch {
            if (!signal.aborted) input.receive({type: 'log-failed'});
          }
        }),
        records: fromPromise<void, GatewayRecordsInput>(async ({input, signal}) => {
          const end = await ports.reader.records(input.runId, input.fromSeq, signal, bytes =>
            input.receive({type: 'records', rows: tailRecordRows(bytes)}),
          );
          if (end === 'missing') input.receive({type: 'records-missing'});
        }),
      },
    }),
    {input, clock: clockFrom(schedule)},
  ).start();
}
