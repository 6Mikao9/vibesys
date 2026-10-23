import {unprovided} from '@vibesys/machine';
import {
  confirmedRelaunchEpoch,
  copyRelaunch,
  copyRelaunchObservation,
  type RelaunchLiveness,
  type RelaunchObservation,
  type RelaunchRequest,
  type RelaunchResponse,
  RunLiveness,
  relaunchTarget,
  sameRelaunchObservation,
} from '@vibesys/runs/intents';
import {assign, fromCallback, setup} from 'xstate';

export type RelaunchEvent =
  | {readonly type: 'relaunch' | 'heartbeat' | 'stale'}
  | {readonly type: 'epoch'; readonly epoch: bigint}
  | {readonly type: 'observe'; readonly observation: RelaunchObservation}
  | {readonly type: 'liveness'; readonly value: RelaunchLiveness};

/** An uncertain launch is never retried automatically; observation stays independent of I/O. */
export const relaunchMachine = setup({
  types: {
    context: {} as {
      readonly request: RelaunchRequest;
      readonly observation: RelaunchObservation;
      readonly liveness: RelaunchLiveness;
      readonly result: RelaunchResponse | null;
      readonly historyEpoch: bigint;
      readonly fresh: boolean;
      readonly attempted: boolean;
    },
    input: {} as {readonly request: RelaunchRequest; readonly observation: RelaunchObservation},
    events: {} as RelaunchEvent,
  },
  actors: {
    launch: unprovided<RelaunchResponse, RelaunchRequest>('launch'),
    liveness: fromCallback<RelaunchEvent>(() => {
      throw new Error('liveness was not provided');
    }),
  },
  actions: {
    livenessUnknown: assign({
      liveness: ({context}) => ({epoch: context.liveness.epoch, value: RunLiveness.UNSPECIFIED}),
    }),
  },
  guards: {
    eligible: ({context}) => relaunchTarget(context.observation, context.liveness) !== null,
    confirmed: ({context}) =>
      context.attempted &&
      confirmedRelaunchEpoch(
        context.request,
        context.observation,
        context.liveness,
        context.historyEpoch,
      ) !== null,
    nextEpoch: ({context}) =>
      context.observation.epoch > context.request.expectedEpoch &&
      relaunchTarget(context.observation, context.liveness) !== null,
  },
  delays: {deadline: 35_000},
}).createMachine({
  id: 'relaunch',
  context: ({input}) => ({
    request: copyRelaunch(input.request),
    observation: copyRelaunchObservation(input.observation),
    liveness: {epoch: 0n, value: RunLiveness.UNSPECIFIED},
    result: null,
    historyEpoch: 0n,
    fresh: false,
    attempted: false,
  }),
  invoke: {
    id: 'liveness',
    src: 'liveness',
    onError: {actions: 'livenessUnknown'},
  },
  on: {
    heartbeat: {actions: assign({fresh: true})},
    stale: {actions: ['livenessUnknown', assign({fresh: false})]},
    epoch: {
      actions: assign({
        historyEpoch: ({context, event}) =>
          event.epoch > context.historyEpoch ? event.epoch : context.historyEpoch,
      }),
    },
    observe: {
      guard: ({context, event}) =>
        event.observation.epoch >= context.observation.epoch &&
        !sameRelaunchObservation(context.observation, event.observation),
      actions: assign(({event}) => ({observation: copyRelaunchObservation(event.observation)})),
    },
    liveness: {
      guard: ({context, event}) =>
        event.value.epoch >= context.liveness.epoch ||
        (event.value.epoch === 0n && event.value.value === RunLiveness.UNSPECIFIED),
      actions: assign(({context, event}) => ({
        liveness:
          event.value.epoch === 0n && event.value.value === RunLiveness.UNSPECIFIED
            ? {epoch: context.liveness.epoch, value: RunLiveness.UNSPECIFIED}
            : event.value,
      })),
    },
  },
  initial: 'ready',
  states: {
    ready: {
      on: {
        relaunch: {
          guard: 'eligible',
          target: 'launching',
          actions: assign(({context}) => ({
            request: copyRelaunch({...context.request, expectedEpoch: context.observation.epoch}),
            result: null,
            attempted: true,
          })),
        },
      },
    },
    launching: {
      always: {guard: 'confirmed', target: 'settled'},
      invoke: {
        id: 'launch',
        src: 'launch',
        input: ({context}) => context.request,
        onDone: {target: 'settled', actions: assign(({event}) => ({result: event.output}))},
        onError: {target: 'uncertain'},
      },
      after: {deadline: 'uncertain'},
    },
    settled: {always: {guard: 'nextEpoch', target: 'ready'}},
    uncertain: {always: {guard: 'confirmed', target: 'settled'}},
  },
});

/** A quiet or broken WatchRuns is re-read; silence cannot renew permission to act. */
export const livenessWatchMachine = setup({
  types: {
    events: {} as {type: 'ended' | 'heartbeat'} | {type: 'liveness'; value: RelaunchLiveness},
  },
  actions: {observed: () => {}, unknown: () => {}},
  actors: {
    read: fromCallback(() => {
      throw new Error('liveness read was not provided');
    }),
  },
  delays: {lease: 30_000, retry: 1000},
}).createMachine({
  id: 'livenessWatch',
  initial: 'watching',
  states: {
    watching: {
      invoke: {id: 'read', src: 'read', onError: 'recovering'},
      initial: 'fresh',
      states: {
        fresh: {
          after: {lease: '#livenessWatch.recovering'},
          on: {
            liveness: {target: 'fresh', reenter: true, actions: 'observed'},
            heartbeat: {target: 'fresh', reenter: true, actions: 'observed'},
          },
        },
      },
      on: {
        ended: 'recovering',
      },
    },
    recovering: {entry: 'unknown', after: {retry: 'watching'}},
  },
});

/** Unresolved facts retain one re-read deadline across unknown/catching-up transitions. */
export const writerRecoveryMachine = setup({
  types: {events: {} as {type: 'observe'; waiting: boolean}},
  actions: {
    refresh: () => {
      throw new Error('writer refresh was not provided');
    },
  },
  delays: {deadline: 30_000},
}).createMachine({
  initial: 'current',
  on: {observe: {guard: ({event}) => !event.waiting, target: '.current'}},
  states: {
    current: {on: {observe: {guard: ({event}) => event.waiting, target: 'waiting'}}},
    waiting: {after: {deadline: 'refreshing'}},
    refreshing: {
      entry: 'refresh',
      always: 'waiting',
    },
  },
});
