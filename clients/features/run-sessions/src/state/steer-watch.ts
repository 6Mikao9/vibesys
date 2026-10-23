import type {Record as StoreRecord} from '@vibesys/runs/chat';
import {assign, fromCallback, setup} from 'xstate';
import {emptySteerFold, foldSteer, type SteerFold} from './steer.js';

export interface SteerWatchContext {
  readonly fold: SteerFold;
}
export type SteerWatchEvent =
  | {readonly type: 'records'; readonly records: readonly StoreRecord[]}
  | {readonly type: 'disconnected'};

/** One tail of the run's log, resumed after the last record folded whenever it drops. */
export const steerWatchMachine = setup({
  types: {context: {} as SteerWatchContext, events: {} as SteerWatchEvent},
  actors: {
    watch: fromCallback<SteerWatchEvent, bigint>(() => {
      throw new Error('watch was not provided');
    }),
  },
  delays: {reconnect: 1000},
}).createMachine({
  id: 'steerWatch',
  context: {fold: emptySteerFold},
  initial: 'watching',
  on: {
    records: {
      actions: assign(({context, event}) => ({fold: foldSteer(context.fold, event.records)})),
    },
  },
  states: {
    watching: {
      invoke: {
        id: 'watch',
        src: 'watch',
        input: ({context}) => context.fold.throughSeq + 1n,
        onError: {target: 'waiting'},
      },
      on: {disconnected: 'waiting'},
    },
    waiting: {after: {reconnect: 'watching'}},
  },
});
