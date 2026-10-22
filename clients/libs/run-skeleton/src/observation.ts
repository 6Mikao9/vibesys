/** The run reader owns independent history and transient host observations. */
import {unprovided} from '@vibesys/machine';
import {assign, fromCallback, setup} from 'xstate';
import type {RunLivenessHint, SkeletonLoad} from './loading.js';

export interface ObservationInput {
  readonly runId: string;
  readonly timeoutMs: number;
}
/** How a reader stands: `missing` is the host's final answer, `failed` a transient loss. */
export type RunReadStatus = 'ok' | 'failed' | 'missing';
export type ObservationEvent =
  | {type: 'read'; load: SkeletonLoad | undefined; status: RunReadStatus}
  | {type: 'hint'; hint: RunLivenessHint | undefined}
  | {type: 'ready'};
export interface ObservationOperation extends ObservationInput {
  readonly receive: (event: ObservationEvent) => void;
}
interface Context extends ObservationInput {
  load: SkeletonLoad | undefined;
  hint: RunLivenessHint | undefined;
  status: RunReadStatus;
  watchFailed: boolean;
}
export const runObservationMachine = setup({
  types: {context: {} as Context, input: {} as ObservationInput, events: {} as ObservationEvent},
  actors: {
    history: fromCallback<ObservationEvent, ObservationOperation>(() => {
      throw new Error('history not provided');
    }),
    watch: unprovided<void, ObservationOperation>('run liveness'),
  },
  delays: {deadline: ({context}) => context.timeoutMs, retry: 1000},
}).createMachine({
  id: 'runObservation',
  context: ({input}) => ({
    ...input,
    load: undefined,
    hint: undefined,
    status: 'ok',
    watchFailed: false,
  }),
  invoke: {
    id: 'history',
    src: 'history',
    input: ({context, self}) => ({...context, receive: event => self.send(event)}),
    onError: {actions: assign({status: 'failed'})},
  },
  on: {
    read: {actions: assign({load: ({event}) => event.load, status: ({event}) => event.status})},
    hint: {actions: assign({hint: ({event}) => event.hint})},
  },
  initial: 'watching',
  states: {
    watching: {
      initial: 'waiting',
      invoke: {
        id: 'watch',
        src: 'watch',
        input: ({context, self}) => ({...context, receive: event => self.send(event)}),
        onDone: 'retrying',
        onError: 'retrying',
      },
      states: {
        waiting: {
          after: {deadline: '#runObservation.retrying'},
          on: {ready: {target: 'live', actions: assign({watchFailed: false})}},
        },
        live: {},
      },
    },
    retrying: {
      entry: assign({watchFailed: true}),
      after: {retry: 'watching'},
    },
  },
});
