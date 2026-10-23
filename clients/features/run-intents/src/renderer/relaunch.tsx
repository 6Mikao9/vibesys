import type {HostKey} from '@vibesys/hosts';
import type {GatewayBridge} from '@vibesys/ipc';
import {clockFrom, type ScheduleTimeout} from '@vibesys/machine';
import {
  type RelaunchLiveness,
  type RelaunchObservation,
  RunLiveness,
  relaunchLabel,
  relaunchRequest,
  relaunchTarget,
} from '@vibesys/runs/intents';
import {useEffect, useRef, useState} from 'react';
import {createActor, type SnapshotFrom} from 'xstate';
import {type relaunchMachine, writerRecoveryMachine} from '../state/index.js';
import {relaunchActor} from './effects/relaunch.js';
import type {RelaunchView} from './ui/relaunch.js';

const UNKNOWN: RelaunchLiveness = {epoch: 0n, value: RunLiveness.UNSPECIFIED};

/** Keep the in-flight request and result while the reader observes its new epoch. */
export function useRelaunch(props: {
  readonly bridge: GatewayBridge;
  readonly host: HostKey;
  readonly projectRoot: string;
  readonly runId: string;
  readonly observation: RelaunchObservation | undefined;
  readonly schedule: ScheduleTimeout;
}): {readonly view: RelaunchView | undefined; readonly liveness: RelaunchLiveness} {
  const [snapshot, setSnapshot] = useState<SnapshotFrom<typeof relaunchMachine> | null>(null);
  const current = useRef<ReturnType<typeof relaunchActor> | null>(null);
  const observation = useRef(props.observation);
  observation.current = props.observation;
  const enabled = props.observation !== undefined;
  useEffect(() => {
    if (!enabled) return;
    const initial = observation.current;
    if (!initial) return;
    const actor = relaunchActor({
      bridge: props.bridge,
      host: props.host,
      request: relaunchRequest(props.projectRoot, props.runId, initial.epoch),
      observation: initial,
      schedule: props.schedule,
    });
    current.current = actor;
    const subscription = actor.subscribe(setSnapshot);
    actor.start();
    return () => {
      current.current = null;
      subscription.unsubscribe();
      actor.stop();
    };
  }, [props.bridge, props.host, props.projectRoot, props.runId, props.schedule, enabled]);
  useEffect(() => {
    if (props.observation) current.current?.send({type: 'observe', observation: props.observation});
  }, [props.observation]);
  if (!enabled || !snapshot) return {view: undefined, liveness: UNKNOWN};
  const liveness = snapshot.context.liveness;
  if (
    snapshot.matches('ready') &&
    relaunchTarget(snapshot.context.observation, snapshot.context.liveness) === null &&
    snapshot.context.result === null
  )
    return {view: undefined, liveness};
  const view: RelaunchView = {
    state:
      snapshot.matches('ready') &&
      relaunchTarget(snapshot.context.observation, snapshot.context.liveness) === null
        ? 'unavailable'
        : snapshot.value,
    result: relaunchLabel(snapshot.context.result),
    appliedEpoch:
      snapshot.context.result?.outcome.case === 'epoch'
        ? snapshot.context.result.outcome.value
        : null,
    available: relaunchTarget(snapshot.context.observation, snapshot.context.liveness) !== null,
    launch: () => current.current?.send({type: 'relaunch'}),
  };
  return {view, liveness};
}

/** The page owns the lifecycle reader; this hook only translates the machine's refresh request. */
export function useWriterRecovery(
  schedule: ScheduleTimeout,
  waiting: boolean,
  refresh: (() => void) | undefined,
) {
  const latest = useRef({refresh, waiting});
  latest.current = {refresh, waiting};
  const current = useRef<ReturnType<typeof createActor<typeof writerRecoveryMachine>> | null>(null);
  useEffect(() => {
    const actor = createActor(
      writerRecoveryMachine.provide({
        actions: {
          refresh: () => latest.current.refresh?.(),
        },
      }),
      {clock: clockFrom(schedule)},
    ).start();
    current.current = actor;
    actor.send({type: 'observe', waiting: latest.current.waiting});
    return () => {
      current.current = null;
      actor.stop();
    };
  }, [schedule]);
  useEffect(() => {
    current.current?.send({type: 'observe', waiting});
  }, [waiting]);
}
