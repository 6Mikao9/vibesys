import type {HostKey} from '@vibesys/hosts';
import type {GatewayBridge} from '@vibesys/ipc';
import {clockFrom, type ScheduleTimeout} from '@vibesys/machine';
import {
  confirmedRelaunchEpoch,
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
  readonly enabled: boolean;
  readonly historyEpoch: bigint;
  readonly schedule: ScheduleTimeout;
}): {
  readonly view: RelaunchView | undefined;
  readonly liveness: RelaunchLiveness;
  readonly fresh: boolean;
} {
  const [snapshot, setSnapshot] = useState<SnapshotFrom<typeof relaunchMachine> | null>(null);
  const current = useRef<ReturnType<typeof relaunchActor> | null>(null);
  const observation = useRef(props.observation);
  observation.current = props.observation;
  const enabled = props.enabled;
  useEffect(() => {
    if (!enabled) return;
    const initial = observation.current ?? {epoch: 0n};
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
  useEffect(() => {
    current.current?.send({type: 'epoch', epoch: props.historyEpoch});
  }, [props.historyEpoch]);
  if (!enabled || !snapshot) return {view: undefined, liveness: UNKNOWN, fresh: false};
  const owned =
    current.current?.getSnapshot() === snapshot &&
    snapshot.context.request.projectRoot === props.projectRoot;
  const liveness = owned ? snapshot.context.liveness : UNKNOWN;
  const confirmed = snapshot.context.attempted
    ? confirmedRelaunchEpoch(
        snapshot.context.request,
        snapshot.context.observation,
        liveness,
        snapshot.context.historyEpoch,
      )
    : null;
  const appliedEpoch =
    confirmed ??
    (snapshot.context.result?.outcome.case === 'epoch'
      ? snapshot.context.result.outcome.value
      : null);
  const view: RelaunchView = {
    state: snapshot.value,
    result:
      appliedEpoch === null
        ? relaunchLabel(snapshot.context.result)
        : `Epoch ${appliedEpoch.toString()}`,
    appliedEpoch,
    available:
      props.observation !== undefined && relaunchTarget(props.observation, liveness) !== null,
    launch: () => {
      if (props.observation)
        current.current?.send({type: 'observe', observation: props.observation});
      current.current?.send({type: 'relaunch'});
    },
  };
  return {view, liveness, fresh: owned && snapshot.context.fresh};
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
