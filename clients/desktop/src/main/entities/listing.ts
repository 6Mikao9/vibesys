/**
 * Pure projections of what a host's helper reports into what the views show: a registry listing
 * into a `HostListing`, and a connection phase into a `ConnectionStatus`.
 */
import type {InstanceList, LiveInstanceRecord} from '@vibesys/backend-client/host-helper';
import type {ConnectionStatus} from '../../common/connection-status.js';
import type {HostKey} from '../../common/hosts.js';
import type {HostListing, WelcomeRun} from '../../common/welcome-protocol.js';
import type {Phase} from '../platform/connection/index.js';

function welcomeRun(record: LiveInstanceRecord): WelcomeRun {
  const {phase} = record;
  return {
    id: record.id,
    projectRoot: record.project_root,
    runId: record.run_id,
    status: phase.stage == null ? phase.status : `${phase.status} (${phase.stage})`,
    startedAt: record.started_at,
    reason: phase.reason == null ? null : phase.reason.message,
  };
}

/** `listing` from `host`'s registry, as the welcome view lists it. */
export function hostListing(host: HostKey, listing: InstanceList): HostListing {
  return {
    kind: 'listed',
    host,
    live: listing.instances.map(welcomeRun),
    ended: (listing.ended ?? []).map(welcomeRun),
    unreadable: [
      ...(listing.unreadable ?? []).map(entry => entry.id),
      ...(listing.unverified ?? []),
    ].sort(),
  };
}

/** The live record of `instanceId` in `listing`, if there is one. */
export function liveRecord(listing: InstanceList, instanceId: string): LiveInstanceRecord | null {
  return listing.instances.find(record => record.id === instanceId) ?? null;
}

/** How a host connection's phase reads in the views. */
export function connectionStatus(phase: Phase): ConnectionStatus {
  switch (phase.kind) {
    case 'idle':
    case 'linking':
    case 'handshaking':
      return {kind: 'connecting'};
    case 'ready':
      return {kind: 'connected'};
    case 'reconnecting':
      return {kind: 'reconnecting', attempt: phase.attempt};
    case 'offline':
      return {kind: 'offline', detail: phase.detail};
    case 'auth-needed':
      return {kind: 'auth-needed', detail: phase.detail};
    case 'incompatible':
      return {kind: 'incompatible', detail: phase.detail};
    case 'closed':
      return {kind: 'closed'};
  }
}
