import {describe, expect, test} from 'bun:test';
import {readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import type {RunEvent, RunOverview, Span} from '@vibesys/contracts';
import {initialCoreState, reduceEventBatch} from '@vibesys/run-state';
import {CandidateFold} from '@vibesys/run-state/candidates';
import {EMPTY_TRACE, foldTrace, traceSpans} from '@vibesys/run-state/trace';
import {SEEDS, Seeded} from '@vibesys/store/testing';
import {implementerOutcomes} from './idea-outcomes.js';
import {EMPTY_TRACE_LOG, traceAt, traceStep} from './live.js';
import {type RunPageState, runPageInitial, runPageStep} from './run-page.js';
import {summaryFacts, type WireSummary, type WireTokens} from './run-summary.js';
import {historyLoading, traceArrival, transcriptLoading} from './views.js';

/** The shared fixture: a journal and the summary the helper folds from it (`runs.summary`). */
const FIXTURE = resolve(
  import.meta.dir,
  '../../../../../tests/conformance/fixtures/run-summary/two-ideas.json',
);

interface WireTally {
  readonly input: number | null;
  readonly cache_read: number | null;
  readonly cache_write: number | null;
  readonly output: number | null;
  readonly reasoning: number | null;
}

interface Fixture {
  readonly events: readonly RunEvent[];
  readonly summary: {
    readonly through_sequence: number;
    readonly event_count: number;
    readonly spans: readonly Span[];
    readonly overview: RunOverview | null;
    readonly replies: readonly {
      readonly span_id: string;
      readonly kind: string;
      readonly json_schema: unknown;
      readonly value: unknown;
    }[];
    readonly tokens: WireTally;
    readonly usage: readonly {readonly span_id: string; readonly tally: WireTally}[];
  };
}

const fixture = JSON.parse(readFileSync(FIXTURE, 'utf8')) as Fixture;

const tokens = (tally: WireTally): WireTokens => ({
  input: tally.input,
  cacheRead: tally.cache_read,
  cacheWrite: tally.cache_write,
  output: tally.output,
  reasoning: tally.reasoning,
});

/** The summary as the host's reader hands it over (`RunSummary` of `@vibesys/ipc`). */
function wire(summary: Fixture['summary']): WireSummary {
  return {
    throughSequence: summary.through_sequence,
    eventCount: summary.event_count,
    journalBytes: 1234,
    overview: summary.overview,
    spans: summary.spans,
    replies: summary.replies.map(reply => ({
      spanId: reply.span_id,
      kind: reply.kind,
      jsonSchema: reply.json_schema,
      value: reply.value,
    })),
    tokens: tokens(summary.tokens),
    usage: summary.usage.map(entry => ({spanId: entry.span_id, tokens: tokens(entry.tally)})),
  };
}

const RUN_ID = '20261010-120000-11111111-dynamic';

describe('a run seeded from its summary holds what the whole journal holds for the run level', () => {
  const facts = summaryFacts(wire(fixture.summary), RUN_ID, 'completed');
  const seeded = reduceEventBatch(initialCoreState(), facts.seed);
  const whole = reduceEventBatch(initialCoreState(), fixture.events);

  test('the spend: the run total and each turn span', () => {
    expect(seeded.tokens).toEqual(whole.tokens);
  });

  test('the idea rows read each implementer’s reply from the seeded core', () => {
    const candidates = new CandidateFold();
    candidates.apply(fixture.events);
    expect(implementerOutcomes(seeded, candidates.tree())).toEqual(
      implementerOutcomes(whole, candidates.tree()),
    );
  });

  test('the snapshot is the run’s spans and cursor', () => {
    expect(facts.snapshot.spans).toEqual(fixture.summary.spans as never);
    expect(facts.snapshot.sequence).toBe(fixture.summary.through_sequence);
    expect(facts.through).toBe(fixture.summary.through_sequence);
  });

  test('a summary that is not a snapshot of a run is refused, naming what is wrong', () => {
    const broken = {...wire(fixture.summary), spans: [{id: 7}]};
    expect(() => summaryFacts(broken, RUN_ID, 'completed')).toThrow();
  });
});

function initial(): RunPageState {
  return runPageInitial({
    identity: {host: 'ssh:host-b', runId: RUN_ID},
    title: {project: 'queue-rs', task: null},
    hostLabel: 'host-b',
    unavailable: {},
  });
}

const ENDED = {
  type: 'record',
  record: {
    kind: 'ended',
    run: {
      id: '0123456789ab',
      phase: 'stopped',
      end: {outcome: 'stopped', reason: 'Stopped by you.', resumable: false},
    },
  },
} as never;

describe('an ended run opened from its summary', () => {
  const facts = summaryFacts(wire(fixture.summary), RUN_ID, 'completed');

  function opened() {
    const asked = runPageStep(initial(), ENDED);
    const request = asked.requests.find(each => each.kind === 'read-summary');
    if (request?.kind !== 'read-summary') throw new Error('no summary asked');
    const state = runPageStep(asked.state, {
      type: 'summary',
      token: request.token,
      reply: {kind: 'summary', ...facts},
      wallMs: 0,
    });
    return {asked, state};
  }

  test('asks for the summary first and no history', () => {
    const {asked} = opened();
    const kinds = asked.requests.map(each => each.kind);
    expect(kinds).toContain('read-summary');
    expect(kinds).not.toContain('read-history');
    expect(traceArrival(asked.state)).toBe('awaited');
  });

  test('is final and interactive at once: spans, spend and replies, with no history asked', () => {
    const {state} = opened();
    expect(state.requests).toEqual([]);
    expect(state.state.history).toEqual({kind: 'idle'});
    expect(traceArrival(state.state)).toBe('arrived');
    expect(historyLoading(state.state)).toBeNull();
    expect(traceSpans(state.state.run.trace.head)).toEqual(fixture.summary.spans);
    expect(state.state.run.core?.tokens).toEqual(
      reduceEventBatch(initialCoreState(), fixture.events).tokens,
    );
  });

  test('reads the transcript only when a pane needs it, behind a progress that is the pane’s', () => {
    const {state} = opened();
    const needed = runPageStep(state.state, {type: 'need-history'});
    expect(needed.requests.map(each => each.kind)).toEqual(['read-history']);
    expect(transcriptLoading(needed.state)).not.toBeNull();
    expect(historyLoading(needed.state)).toBeNull();
    expect(traceArrival(needed.state)).toBe('arrived');
    // Asking again while it reads, or once it is read, asks for nothing more.
    expect(runPageStep(needed.state, {type: 'need-history'}).requests).toEqual([]);
  });

  test('a transcript that cannot be read leaves the run level standing', () => {
    const {state} = opened();
    const needed = runPageStep(state.state, {type: 'need-history'});
    const request = needed.requests[0];
    if (request?.kind !== 'read-history') throw new Error('no history asked');
    const failed = runPageStep(needed.state, {
      type: 'history',
      token: request.token,
      reply: {kind: 'failed', message: 'channel closed'},
    });
    expect(failed.state.run).toBe(needed.state.run);
    expect(traceSpans(failed.state.run.trace.head)).toEqual(fixture.summary.spans);
    expect(traceArrival(failed.state)).toBe('arrived');
  });

  test('a host without history keeps a ready summary visible', () => {
    const {state} = opened();
    const needed = runPageStep(state.state, {type: 'need-history'});
    const request = needed.requests[0];
    if (request?.kind !== 'read-history') throw new Error('no history asked');
    const unsupported = runPageStep(needed.state, {
      type: 'history',
      token: request.token,
      reply: {kind: 'unsupported'},
    });
    expect(traceArrival(unsupported.state)).toBe('arrived');
    expect(traceSpans(unsupported.state.run.trace.head)).toEqual(fixture.summary.spans);
  });

  test('a host with no summary is read through its history, as before', () => {
    const asked = runPageStep(initial(), ENDED);
    const request = asked.requests.find(each => each.kind === 'read-summary');
    if (request?.kind !== 'read-summary') throw new Error('no summary asked');
    const older = runPageStep(asked.state, {
      type: 'summary',
      token: request.token,
      reply: {kind: 'unsupported'},
      wallMs: 0,
    });
    expect(older.requests.map(each => each.kind)).toEqual(['read-history']);
  });

  test('a summary that fails to read fails the run’s history, naming why', () => {
    const asked = runPageStep(initial(), ENDED);
    const request = asked.requests.find(each => each.kind === 'read-summary');
    if (request?.kind !== 'read-summary') throw new Error('no summary asked');
    const failed = runPageStep(asked.state, {
      type: 'summary',
      token: request.token,
      reply: {kind: 'failed', message: 'channel closed'},
      wallMs: 0,
    });
    expect(failed.state.history).toEqual({kind: 'failed', message: 'channel closed'});
  });

  test('a late or repeated summary reply is dropped by its token', () => {
    const {state} = opened();
    const again = runPageStep(state.state, {
      type: 'summary',
      token: 1,
      reply: {kind: 'summary', ...facts},
      wallMs: 0,
    });
    expect(again.state).toBe(state.state);
  });
});

/** A journal of span events: spans start, update and end, in sequence order with the odd skip. */
function spanEvents(random: Seeded, count: number): RunEvent[] {
  const ids = ['a', 'b', 'c', 'd', 'e'];
  const events: RunEvent[] = [];
  let sequence = 0;
  for (let index = 0; index < count; index += 1) {
    sequence += random.int(7) === 0 ? 2 : 1;
    const id = random.pick(ids);
    const kind = random.pick(['span_started', 'span_updated', 'span_ended']);
    events.push({
      protocol_version: 1,
      sequence,
      type: kind,
      timestamp: '2026-10-15T16:30:51Z',
      data: {
        kind,
        span: {
          id,
          parent: null,
          type: 'flow',
          name: id,
          status: kind === 'span_ended' ? 'done' : 'running',
          started_at: index,
        },
      },
    } as unknown as RunEvent);
  }
  return events;
}

describe('the summary and the live tail meet with no gap and no duplicate', () => {
  test('for any cut, tail batching and overlap, the run is the whole journal’s fold', () => {
    for (const seed of SEEDS) {
      const random = new Seeded(seed);
      const journal = spanEvents(random, random.int(60));
      const whole = foldTrace(EMPTY_TRACE, journal);
      const cut = random.int(journal.length + 1);
      const prefix = foldTrace(EMPTY_TRACE, journal.slice(0, cut));
      const through = journal.slice(0, cut).at(-1)?.sequence ?? 0;

      let log = traceStep(EMPTY_TRACE_LOG, {
        kind: 'snapshot',
        snapshot: {
          run_id: RUN_ID,
          sequence: through,
          status: 'running',
          spans: [...traceSpans(prefix)],
        },
      });
      // The tail arrives in batches that may start early (a replay from before the summary's
      // cursor) and may repeat events a batch before.
      for (let from = cut; from < journal.length; ) {
        const early = Math.max(0, from - random.int(4));
        const to = Math.min(journal.length, from + 1 + random.int(5));
        log = traceStep(log, {kind: 'events', events: journal.slice(early, to)});
        from = to;
      }
      expect(traceSpans(traceAt(log, null)), `seed ${seed}`).toEqual(traceSpans(whole));
      expect(traceAt(log, null).sequence, `seed ${seed}`).toBe(whole.sequence);
    }
  });
});
