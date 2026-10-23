import type {HostKey, RecordedProjectRoot} from '@vibesys/hosts';
import type {GatewayBridge} from '@vibesys/ipc';
import type {ScheduleTimeout} from '@vibesys/machine';
import {newRequestId} from '@vibesys/request-identity';
import {
  type RelaunchObservation,
  type RunIntentAction,
  runIntent,
  runReference,
  writerControls,
} from '@vibesys/runs/intents';
import {type JSX, useEffect, useRef, useState} from 'react';
import type {SnapshotFrom} from 'xstate';
import {historyEpoch, intentRows, type runIntentsMachine} from '../state/index.js';
import {intentsActor} from './effects/controls.js';
import {useRelaunch, useWriterRecovery} from './relaunch.js';
import {IntentControls} from './ui/controls.js';

/** Mount with a key of host/runId. enabled is the host's runTransportV2 setting. */
export function useRunIntents(props: {
  readonly bridge: GatewayBridge;
  readonly host: HostKey;
  readonly projectRoot: RecordedProjectRoot | undefined;
  readonly runId: string;
  readonly enabled: boolean;
  readonly schedule: ScheduleTimeout;
  readonly relaunch?: RelaunchObservation;
  readonly refreshWriter?: () => void;
}) {
  const [snapshot, setSnapshot] = useState<SnapshotFrom<typeof runIntentsMachine> | null>(null);
  const [text, setText] = useState('');
  const [guidanceOpen, showGuidance] = useState(false);
  const current = useRef<ReturnType<typeof intentsActor> | null>(null);
  useEffect(() => {
    const actor = intentsActor({
      bridge: props.bridge,
      host: props.host,
      runId: props.runId,
      enabled: props.enabled,
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
  }, [props.bridge, props.host, props.runId, props.enabled, props.schedule]);
  const projectRoot = props.projectRoot;
  const {view: machineRelaunch, liveness} = useRelaunch({
    bridge: props.bridge,
    host: props.host,
    projectRoot: projectRoot ?? '',
    runId: props.runId,
    observation: props.enabled && projectRoot !== undefined ? props.relaunch : undefined,
    schedule: props.schedule,
  });
  const busy = snapshot?.matches({active: 'sending'}) ?? false;
  const sendRequest = (
    request: Parameters<NonNullable<typeof current.current>['send']>[0] & {type: 'send'},
  ) => current.current?.send(request);
  const committed = snapshot ? historyEpoch(snapshot.context) : 0n;
  const acknowledged = machineRelaunch?.appliedEpoch ?? 0n;
  const writer = writerControls({
    observation: props.relaunch,
    liveness,
    historyEpoch: committed,
    acknowledgedEpoch: acknowledged,
  });
  useWriterRecovery(
    props.schedule,
    props.enabled && writer.status !== 'ready',
    props.refreshWriter,
  );
  if (!props.enabled || !snapshot || projectRoot === undefined)
    return {controls: null, busy, sendRequest};
  const relaunch = machineRelaunch && {
    ...machineRelaunch,
    available: machineRelaunch.available && writer.relaunch !== null,
  };
  const confirmedEpoch = relaunch?.appliedEpoch ?? 0n;
  const epoch = writer.epoch > confirmedEpoch ? writer.epoch : confirmedEpoch;
  const actions = writer.actions;
  const send = (action: RunIntentAction) => {
    if (!actions.includes(action)) return;
    const request = runIntent(
      {
        ...runReference(projectRoot, props.runId),
        requestId: newRequestId(),
        expectedEpoch: props.relaunch?.epoch ?? 0n,
      },
      action,
      text,
    );
    current.current?.send({type: 'send', request});
    if (action === 'steer') showGuidance(false);
  };
  const controls = (
    <IntentControls
      {...(relaunch ? {relaunch} : {})}
      actions={actions}
      status={writer.status}
      epoch={epoch}
      busy={snapshot.matches({active: 'sending'})}
      entries={intentRows(snapshot.context, epoch)}
      error={snapshot.context.error}
      warning={snapshot.context.warning}
      guidanceOpen={guidanceOpen}
      showGuidance={showGuidance}
      text={text}
      edit={setText}
      send={send}
      retry={requestId => current.current?.send({type: 'retry', requestId})}
    />
  );
  return {controls, busy, sendRequest};
}

export function RunIntents(props: Parameters<typeof useRunIntents>[0]): JSX.Element | null {
  return useRunIntents(props).controls;
}

export {intentsActor} from './effects/controls.js';
export {relaunchActor} from './effects/relaunch.js';
