import {describe, expect, test} from 'bun:test';
import {readdirSync, readFileSync} from 'node:fs';
import {resolve} from 'node:path';
import {type RunEvent, validateRunEvent} from '@vibesys/contracts';
import {initialCoreState, reduceEventBatch} from './index.js';

const EVENT_CORPUS = resolve(import.meta.dir, '../../../../tests/conformance/events');
const EVENT_PAYLOAD_CORPUS = resolve(
  import.meta.dir,
  '../../../../tests/conformance/event-payloads',
);

function eventFixtures(): Array<{name: string; event: RunEvent}> {
  return readdirSync(EVENT_CORPUS)
    .filter(name => name.endsWith('.json'))
    .sort()
    .map(name => ({
      name,
      event: validateRunEvent(JSON.parse(readFileSync(resolve(EVENT_CORPUS, name), 'utf8'))),
    }));
}

function eventPayloadFixtures(): Array<{name: string; event: RunEvent}> {
  return readdirSync(EVENT_PAYLOAD_CORPUS)
    .filter(name => name.endsWith('.jsonl'))
    .sort()
    .flatMap(name =>
      readFileSync(resolve(EVENT_PAYLOAD_CORPUS, name), 'utf8')
        .trimEnd()
        .split('\n')
        .map((line, index) => ({
          name: `${name} case ${index + 1}`,
          event: validateRunEvent(JSON.parse(line)),
        })),
    );
}

describe('shared conformance event corpus', () => {
  for (const fixture of eventFixtures()) {
    test(`${fixture.name} folds through core state`, () => {
      expect(() => reduceEventBatch(initialCoreState(), [fixture.event])).not.toThrow();
    });
  }

  test('batching does not change the final fold', () => {
    const events = eventFixtures().map(({event}, index) => ({...event, sequence: index + 1}));
    const batched = reduceEventBatch(initialCoreState(), events);
    const incremental = events.reduce(
      (state, event) => reduceEventBatch(state, [event]),
      initialCoreState(),
    );

    expect(incremental).toEqual(batched);
  });
});

describe('authoritative event-payload corpus', () => {
  for (const fixture of eventPayloadFixtures()) {
    test(`${fixture.name} validates and folds through core state`, () => {
      expect(() => reduceEventBatch(initialCoreState(), [fixture.event])).not.toThrow();
    });
  }
});
