import {RunLiveness} from '@vibesys/proto';
import type {RelaunchLiveness, RelaunchObservation, RelaunchTarget} from './relaunch.js';

/** The actions the visible controls offer. */
export type RunIntentAction = 'pause' | 'pauseNow' | 'steer';

/** Everything the host has said about the writer: each fact carries the epoch it describes. */
export interface WriterFacts {
  readonly observation: RelaunchObservation | undefined;
  readonly liveness: RelaunchLiveness;
  /** The newest epoch in committed history, which can lead the lifecycle stream. */
  readonly historyEpoch: bigint;
  readonly acknowledgedEpoch?: bigint;
}

export type WriterControls = {readonly epoch: bigint} & (
  | {
      readonly status: 'ready';
      readonly actions: readonly [RunIntentAction, ...RunIntentAction[]];
      readonly relaunch: null;
    }
  | {
      readonly status: 'ready';
      readonly actions: readonly RunIntentAction[];
      readonly relaunch: RelaunchTarget;
    }
  | {
      readonly status: 'catching-up' | 'unknown';
      readonly actions: readonly RunIntentAction[];
      readonly relaunch: null;
    }
);

/**
 * Facts are epoch-fenced: lifecycle is known only at its recorded epoch; history and launch
 * acknowledgements can prove a successor exists before lifecycle catches up. WatchRuns is
 * known until its subscription ends or its 30-second lease expires, then unknown (never alive).
 * The total projection yields actions, Resume, or a visible catching-up/unknown reason.
 * The liveness watch resubscribes within 31 seconds; unresolved facts ask the owning page to
 * re-read lifecycle within 30 seconds, repeatedly while the gap persists. Machine/DOM properties
 * enforce these bounds. Resume follows the host contract: every ending is eligible.
 */
export function writerControls(facts: WriterFacts): WriterControls {
  const {observation, liveness, historyEpoch, acknowledgedEpoch = 0n} = facts;
  const observed = observation?.epoch ?? 0n;
  const epoch = [observed, liveness.epoch, historyEpoch, acknowledgedEpoch].reduce((a, b) =>
    a > b ? a : b,
  );
  const waiting = (status: 'catching-up' | 'unknown'): WriterControls => ({
    epoch,
    actions: [],
    relaunch: null,
    status,
  });
  if (!observation || observed < 1n || observed !== epoch) return waiting('catching-up');
  const live = liveness.epoch === observed ? liveness.value : RunLiveness.UNSPECIFIED;
  const state = observation.lifecycle?.state;
  if (live === RunLiveness.ALIVE) {
    const actions = liveActions(observation);
    return actions ? {epoch, actions, relaunch: null, status: 'ready'} : waiting('catching-up');
  }
  if (live === RunLiveness.INTERRUPTED || state?.case === 'parked' || state?.case === 'ended')
    return {
      epoch,
      actions: [],
      status: 'ready',
      relaunch: {
        epoch,
        state: state?.case === 'parked' || state?.case === 'ended' ? state.case : 'interrupted',
      },
    };
  return waiting(live === RunLiveness.ENDED ? 'catching-up' : 'unknown');
}

function liveActions(
  observation: RelaunchObservation,
): readonly [RunIntentAction, ...RunIntentAction[]] | null {
  const state = observation.lifecycle?.state;
  switch (state?.case) {
    case 'starting':
      return ['pause', 'pauseNow'];
    case 'running':
      return state.value.hold?.reason.case === 'operator'
        ? ['pauseNow', 'steer']
        : ['pause', 'pauseNow', 'steer'];
    case 'draining':
      return ['pauseNow'];
    default:
      return null;
  }
}

/** The relaunch half of the projection, for the relaunch machine's guards. */
export function relaunchTarget(
  observation: RelaunchObservation,
  liveness: RelaunchLiveness,
): RelaunchTarget | null {
  return writerControls({observation, liveness, historyEpoch: 0n}).relaunch;
}
