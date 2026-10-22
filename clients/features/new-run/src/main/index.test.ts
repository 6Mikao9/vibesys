import {describe, expect, test} from 'bun:test';
import {ConnectionManager} from '@vibesys/connection';
import {FakeLink} from '@vibesys/connection/testing';
import type {HostId} from '@vibesys/hosts';
import type {StoredRecentRun} from '@vibesys/settings';
import {FakeClock} from '@vibesys/transport/testing';
import {NewRunMain} from './index.js';

const LOCAL = {kind: 'local'} as const;

function subject() {
  const link = new FakeLink();
  const manager = new ConnectionManager({
    linkFor: () => link,
    appVersion: 'test',
    scheduleTimeout: new FakeClock().scheduleTimeout,
    config: {delaysMs: [1], deadlineMs: 60_000, heartbeatMs: 15_000},
    log: () => {},
  });
  const remembered: StoredRecentRun[] = [];
  const main = new NewRunMain({
    connect: host => manager.acquire(host, '/checkout'),
    remember: async run => {
      remembered.push(run);
    },
    now: () => 42,
  });
  return {link, main, remembered};
}

describe('NewRunMain', () => {
  test('a v2 start uses the registry and does not ask the helper', async () => {
    const link = new FakeLink();
    const manager = new ConnectionManager({
      linkFor: () => link,
      appVersion: 'test',
      scheduleTimeout: new FakeClock().scheduleTimeout,
      config: {delaysMs: [1], deadlineMs: 60_000, heartbeatMs: 15_000},
      log: () => {},
    });
    const remembered: StoredRecentRun[] = [];
    const watched: HostId[] = [];
    const main = new NewRunMain({
      connect: host => manager.acquire(host, '/checkout'),
      remember: async run => {
        remembered.push(run);
      },
      now: () => 42,
      registryStart: async () => ({runId: 'run-9', instanceId: 'inst-9'}),
      watchRuns: {
        start(host) {
          watched.push(host);
        },
      },
    });
    const started = await main.answer({
      kind: 'start-run',
      host: LOCAL,
      inputs: {
        project: '/p',
        task: 'mpmc',
        harness: null,
        options: {},
        agents: {},
        measure_on: {kind: 'same_machine'},
        args: [],
      },
    });
    expect(started).toEqual({runId: 'run-9', instanceId: 'inst-9'});
    expect(link.helper.requests.some(request => request.type === 'run.start')).toBe(false);
    expect(watched).toEqual([LOCAL]);
    expect(remembered[0]?.runId).toBe('run-9');
  });

  test('a v2 create, copy, and destination use the registry and do not ask the helper', async () => {
    const link = new FakeLink();
    const manager = new ConnectionManager({
      linkFor: () => link,
      appVersion: 'test',
      scheduleTimeout: new FakeClock().scheduleTimeout,
      config: {delaysMs: [1], deadlineMs: 60_000, heartbeatMs: 15_000},
      log: () => {},
    });
    const calls: string[] = [];
    const main = new NewRunMain({
      connect: host => manager.acquire(host, '/checkout'),
      remember: async () => {},
      now: () => 1,
      registryProjects: {
        destination: async () => {
          calls.push('destination');
          return {state: 'absent', project: null};
        },
        create: async () => {
          calls.push('create');
          return {kind: 'created', created: {project: '/work/queue', tasks: ['a']}};
        },
        copy: async () => {
          calls.push('copy');
          return {project: '/own', tasks: ['a']};
        },
      },
    });
    expect(
      await main.answer({
        kind: 'example-destination',
        host: LOCAL,
        example: 'queue',
        destination: '/work/queue',
      }),
    ).toEqual({kind: 'offered', value: {state: 'absent', project: null}});
    expect(
      await main.answer({
        kind: 'create-from-example',
        host: LOCAL,
        example: 'queue',
        destination: '/work/queue',
      }),
    ).toEqual({kind: 'created', created: {project: '/work/queue', tasks: ['a']}});
    expect(
      await main.answer({
        kind: 'copy-out',
        host: LOCAL,
        source: '/nested',
        destination: '/own',
      }),
    ).toEqual({project: '/own', tasks: ['a']});
    expect(calls).toEqual(['destination', 'create', 'copy']);
    expect(link.helper.requests).toEqual([]);
  });

  test("answers the features the machine's helper agreed to in hello", async () => {
    const {link, main} = subject();
    link.helper.features.delete('run_options');
    link.helper.features.delete('examples');
    const features = (await main.answer({kind: 'helper-features', host: LOCAL})) as string[];
    expect(features).toContain('preflight');
    expect(features).not.toContain('run_options');
    expect(features).not.toContain('examples');
  });
});
