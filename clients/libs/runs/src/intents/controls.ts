import {RunLiveness} from '@vibesys/proto';
import type {RunIntentAction} from './index.js';
import type {RelaunchLiveness, RelaunchObservation, RelaunchTarget} from './relaunch.js';

/** Everything the host has said about the writer: each fact carries the epoch it describes. */
export interface WriterFacts {
  readonly observation: RelaunchObservation | undefined;
  readonly liveness: RelaunchLiveness;
  /** The newest epoch in committed history, which can lead the lifecycle stream. */
  readonly historyEpoch: bigint;
}

export interface WriterControls {
  /** The newest epoch any fact names; rows from earlier epochs are history, not current. */
  readonly epoch: bigint;
  readonly actions: readonly RunIntentAction[];
  readonly relaunch: RelaunchTarget | null;
}

/**
 * The one projection of what the user may do to the writer. A fact older than the newest epoch
 * is stale and offers nothing until the stream for that epoch catches up, whatever order the
 * lifecycle, history and liveness streams deliver in. A dead writer (interrupted) accepts no
 * live action. Relaunch follows the host's admission, which accepts every ending, so the
 * ended flag the host keeps for older readers does not narrow it here.
 */
export function writerControls(facts: WriterFacts): WriterControls {
  const {observation, liveness, historyEpoch} = facts;
  const observed = observation?.epoch ?? 0n;
  const epoch = [observed, liveness.epoch, historyEpoch].reduce((a, b) => (a > b ? a : b));
  if (!observation || observed < 1n || observed !== epoch)
    return {epoch, actions: [], relaunch: null};
  const live = liveness.epoch === observed ? liveness.value : RunLiveness.UNSPECIFIED;
  const state = observation.lifecycle?.state;
  if (live === RunLiveness.ALIVE) return {epoch, actions: liveActions(observation), relaunch: null};
  if (live === RunLiveness.INTERRUPTED)
    return {
      epoch,
      actions: [],
      relaunch: {
        epoch,
        state: state?.case === 'parked' || state?.case === 'ended' ? state.case : 'interrupted',
      },
    };
  return {
    epoch,
    actions: liveActions(observation),
    relaunch:
      state?.case === 'parked' || state?.case === 'ended' ? {epoch, state: state.case} : null,
  };
}

function liveActions(observation: RelaunchObservation): readonly RunIntentAction[] {
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
      return [];
  }
}

/** The relaunch half of the projection, for the relaunch machine's guards. */
export function relaunchTarget(
  observation: RelaunchObservation,
  liveness: RelaunchLiveness,
): RelaunchTarget | null {
  return writerControls({observation, liveness, historyEpoch: 0n}).relaunch;
}
