import {expect, test} from 'bun:test';
import {isTerminalPhase, parseCampaignFrame} from './campaign-frames.js';

const validWorkstream = {
  id: 'ws-1',
  title: 'Prefix reuse',
  hypothesis: 'Reuse prefixes',
  startedAt: '2026-01-01T00:00:00Z',
  firstSequence: 1,
  phase: 'implementing',
  active: true,
  finishedAt: null,
  lastSequence: null,
  outcome: null,
  outcomeSummary: '',
};

const validAttempt = {
  attempt: 2,
  attemptLimit: 5,
  refunded: 1,
  lastError: 'transient provider error',
  setupFailure: false,
  repeatedError: false,
};

const validActivity = {
  mode: 'tool',
  summary: 'Using run_benchmark',
  tool: 'run_benchmark',
};

test('parses every frame kind', () => {
  expect(parseCampaignFrame({kind: 'campaign-init', seq: 1, header: {}}).kind).toBe(
    'campaign-init',
  );
  expect(parseCampaignFrame({kind: 'agent-upsert', seq: 2, agent: {}}).kind).toBe('agent-upsert');
  expect(
    parseCampaignFrame({kind: 'workstream-upsert', seq: 3, workstream: validWorkstream}).kind,
  ).toBe('workstream-upsert');
  expect(parseCampaignFrame({kind: 'measurement', seq: 4, measurement: {}}).kind).toBe(
    'measurement',
  );
  expect(parseCampaignFrame({kind: 'tokens', seq: 5, workstreamId: 'ws-1', tokens: 10}).kind).toBe(
    'tokens',
  );
  expect(
    parseCampaignFrame({
      kind: 'workstream-attempt',
      seq: 6,
      workstreamId: 'ws-1',
      attempt: validAttempt,
    }).kind,
  ).toBe('workstream-attempt');
  expect(
    parseCampaignFrame({
      kind: 'workstream-activity',
      seq: 7,
      workstreamId: 'ws-1',
      activity: validActivity,
    }).kind,
  ).toBe('workstream-activity');
  expect(parseCampaignFrame({kind: 'status', seq: 8, status: 'completed'}).kind).toBe('status');
});

test('rejects an unknown frame kind and names the path', () => {
  expect(() => parseCampaignFrame({kind: 'nope', seq: 1})).toThrow(/\$\.kind/);
});

test('rejects an unexpected key and names it', () => {
  expect(() => parseCampaignFrame({kind: 'status', seq: 1, status: 'active', extra: true})).toThrow(
    /\$\.extra/,
  );
});

test('rejects a missing key and names it', () => {
  expect(() => parseCampaignFrame({kind: 'status', seq: 1})).toThrow(/\$\.status/);
});

test('rejects a wrong-typed seq and names it', () => {
  expect(() => parseCampaignFrame({kind: 'status', seq: '1', status: 'active'})).toThrow(/\$\.seq/);
});

test('rejects an invalid workstream phase and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-upsert',
      seq: 1,
      workstream: {...validWorkstream, phase: 'bogus'},
    }),
  ).toThrow(/\$\.workstream\.phase/);
});

test('rejects an unexpected workstream key and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-upsert',
      seq: 1,
      workstream: {...validWorkstream, surprise: 1},
    }),
  ).toThrow(/\$\.workstream\.surprise/);
});

test('rejects an invalid status value', () => {
  expect(() => parseCampaignFrame({kind: 'status', seq: 1, status: 'paused'})).toThrow(
    /\$\.status/,
  );
});

test('rejects a non-object frame', () => {
  expect(() => parseCampaignFrame(null)).toThrow(/\$/);
  expect(() => parseCampaignFrame([])).toThrow(/\$/);
});

test('isTerminalPhase treats parked as still live, like implementing', () => {
  expect(isTerminalPhase('parked')).toBe(false);
  expect(isTerminalPhase('implementing')).toBe(false);
  expect(isTerminalPhase('evaluated')).toBe(true);
  expect(isTerminalPhase('failed')).toBe(true);
  expect(isTerminalPhase('cancelled')).toBe(true);
});

test('rejects an invalid workstream outcome and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-upsert',
      seq: 1,
      workstream: {...validWorkstream, outcome: 'maybe'},
    }),
  ).toThrow(/\$\.workstream\.outcome/);
});

test('rejects a wrong-typed finishedAt and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-upsert',
      seq: 1,
      workstream: {...validWorkstream, finishedAt: 1234},
    }),
  ).toThrow(/\$\.workstream\.finishedAt/);
});

test('rejects a wrong-typed lastSequence and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-upsert',
      seq: 1,
      workstream: {...validWorkstream, lastSequence: 'nine'},
    }),
  ).toThrow(/\$\.workstream\.lastSequence/);
});

test('rejects a wrong-typed tokens value and names the path', () => {
  expect(() =>
    parseCampaignFrame({kind: 'tokens', seq: 1, workstreamId: 'ws-1', tokens: '10'}),
  ).toThrow(/\$\.tokens/);
});

test('rejects a wrong-typed tokens workstreamId and names the path', () => {
  expect(() => parseCampaignFrame({kind: 'tokens', seq: 1, workstreamId: 7, tokens: 10})).toThrow(
    /\$\.workstreamId/,
  );
});

test('rejects an invalid attempt mode value and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-attempt',
      seq: 1,
      workstreamId: 'ws-1',
      attempt: {...validAttempt, attempt: '2'},
    }),
  ).toThrow(/\$\.attempt\.attempt/);
});

test('rejects an unexpected attempt key and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-attempt',
      seq: 1,
      workstreamId: 'ws-1',
      attempt: {...validAttempt, surprise: true},
    }),
  ).toThrow(/\$\.attempt\.surprise/);
});

test('accepts a null lastError on an attempt frame', () => {
  expect(
    parseCampaignFrame({
      kind: 'workstream-attempt',
      seq: 1,
      workstreamId: 'ws-1',
      attempt: {...validAttempt, lastError: null},
    }).kind,
  ).toBe('workstream-attempt');
});

test('rejects an invalid activity mode and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-activity',
      seq: 1,
      workstreamId: 'ws-1',
      activity: {...validActivity, mode: 'sleeping'},
    }),
  ).toThrow(/\$\.activity\.mode/);
});

test('rejects an unexpected activity key and names the nested path', () => {
  expect(() =>
    parseCampaignFrame({
      kind: 'workstream-activity',
      seq: 1,
      workstreamId: 'ws-1',
      activity: {...validActivity, surprise: true},
    }),
  ).toThrow(/\$\.activity\.surprise/);
});

test('accepts a null tool on an activity frame', () => {
  expect(
    parseCampaignFrame({
      kind: 'workstream-activity',
      seq: 1,
      workstreamId: 'ws-1',
      activity: {...validActivity, mode: 'thinking', tool: null},
    }).kind,
  ).toBe('workstream-activity');
});
