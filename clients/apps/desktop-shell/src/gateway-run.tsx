/** The gateway owns every run reader. */

import {useRunIntents} from '@vibesys/feature-run-intents/renderer';
import type {RunPageAddress, RunPageNavigation} from '@vibesys/feature-run-page/renderer';
import {type GatewayRunPorts, RunScreen, useGatewayRun} from '@vibesys/feature-run-page/renderer';
import {type RunPageSeed, recordedRunRoot} from '@vibesys/feature-run-page/state';
import type {GatewayBridge} from '@vibesys/ipc';
import type {ScheduleTimeout} from '@vibesys/machine';
import {observeRun, type Skeleton} from '@vibesys/run-skeleton';
import {gatewayContentCache} from '@vibesys/run-skeleton/cache';
import {gatewayReader} from '@vibesys/run-skeleton/reader';
import type {RelaunchObservation} from '@vibesys/runs/intents';
import {Card, EmptyState, Page, Text} from '@vibesys/ui';
import {type JSX, useMemo} from 'react';
import {useGatewayChat} from './gateway-chat.js';
import {gatewayTransport} from './gateway-transport.js';

export function GatewayRun(props: {
  readonly seed: RunPageSeed;
  readonly bridge: GatewayBridge;
  readonly schedule: ScheduleTimeout;
  readonly address: RunPageAddress;
  readonly navigation: RunPageNavigation;
}): JSX.Element {
  const {bridge, seed, schedule, address} = props;
  const ports = useMemo<GatewayRunPorts>(
    () => ({
      reader: gatewayReader(
        gatewayTransport(bridge, seed.identity.host),
        gatewayContentCache(indexedDB),
        schedule,
      ),
    }),
    [bridge, seed.identity.host, schedule],
  );
  const page = useGatewayRun({seed, ports, schedule, address});
  const {snapshot} = page;
  const observation = observationOf(snapshot.context.load?.skeleton);
  const projectRoot = recordedRunRoot(snapshot.context.load?.skeleton);
  const intents = useRunIntents({
    bridge,
    host: seed.identity.host,
    runId: seed.identity.runId,
    projectRoot,
    ...(observation ? {relaunch: observation} : {}),
    schedule,
  });
  const sessions = useGatewayChat({
    bridge,
    host: seed.identity.host,
    runId: seed.identity.runId,
    projectRoot: projectRoot ?? '',
    schedule,
    core: page.state?.run.core ?? null,
    epoch: observation?.epoch ?? 0n,
    readOnly: observation?.lifecycle?.state.case === 'ended',
  });
  if (
    page.state === null ||
    page.model === null ||
    page.whole === null ||
    !snapshot.matches('active')
  ) {
    return (
      <Page>
        <Card>
          <EmptyState title={snapshot.context.error ?? page.projectionError ?? 'Loading'} />
        </Card>
      </Page>
    );
  }
  return (
    <RunScreen
      state={page.state}
      model={page.model}
      whole={page.whole}
      core={page.state.run.core}
      route={page.route}
      zoom={page.zoom}
      dispatch={action => {
        if (action.type === 'run-again') props.navigation.runAgain();
        else if (action.type === 'open-window') props.navigation.openWindow();
        else page.dispatch(action);
      }}
      chat={sessions}
      toolbar={intents.controls}
      supplemental={
        snapshot.context.error ? <Text tone="err">{snapshot.context.error}</Text> : null
      }
    />
  );
}

function observationOf(skeleton: Skeleton | undefined): RelaunchObservation | undefined {
  if (!skeleton) return undefined;
  const {epoch, lifecycle} = observeRun(skeleton);
  if (epoch === 0) return undefined;
  return {epoch: BigInt(epoch), ...(lifecycle ? {lifecycle} : {})};
}
