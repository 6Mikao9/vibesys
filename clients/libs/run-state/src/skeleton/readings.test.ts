/** The input's value is found whichever order its gates ran in. */
import {expect, test} from 'bun:test';
import {create} from '@bufbuild/protobuf';
import type {Span} from '@vibesys/contracts';
import {
  Direction,
  EpochStartedSchema,
  EpochVersionSchema,
  MetricHistorySchema,
  MetricPointSchema,
  MetricSeriesDeclSchema,
  MetricVersionSchema,
  ObjectiveSchema,
  RunDescriptionSchema,
} from '@vibesys/proto';
import {measuredSpans} from './readings.js';
import type {StoreView} from './types.js';

const gate = (id: string, subject: string): Span =>
  ({
    id,
    label: {kind: 'gate', attrs_json: JSON.stringify({subject, stage: 'GATE_STAGE_BENCHMARK'})},
  }) as unknown as Span;

function view(measured: Record<string, number>): StoreView {
  const decl = create(MetricSeriesDeclSchema, {
    name: 'speed',
    unit: 'ops',
    direction: Direction.MAX,
  });
  const epoch = create(EpochStartedSchema, {
    run: create(RunDescriptionSchema, {
      objectives: [create(ObjectiveSchema, {series: 'speed', direction: Direction.MAX})],
    }),
    series: [decl],
  });
  return {
    seq: 100n,
    spans: {},
    lifecycle: undefined,
    epochs: [create(EpochVersionSchema, {seq: 1n, epoch})],
    metrics: [
      create(MetricHistorySchema, {
        series: 'speed',
        points: Object.entries(measured).map(([span, value], at) =>
          create(MetricVersionSchema, {
            seq: BigInt(2 + at),
            point: create(MetricPointSchema, {series: 'speed', span, value, clock: at}),
          }),
        ),
      }),
    ],
  };
}

/** Every ordering of `items`. */
function orderings<T>(items: readonly T[]): T[][] {
  if (items.length <= 1) return [[...items]];
  return items.flatMap((item, at) =>
    orderings([...items.slice(0, at), ...items.slice(at + 1)]).map(rest => [item, ...rest]),
  );
}

test('the input value is the measuring input gate, whatever the order of the input gates', () => {
  const candidate = 'gate:vs-a:benchmark';
  for (const count of [1, 2, 3]) {
    const gates = Array.from({length: count}, (_, at) => `gate:input:${at}`);
    for (const measuring of gates) {
      for (const order of orderings([...gates, candidate])) {
        const spans = order.map(id =>
          gate(id, id === candidate ? 'GATE_SUBJECT_CANDIDATE' : 'GATE_SUBJECT_INPUT'),
        );
        const row = measuredSpans(spans, view({[measuring]: 100, [candidate]: 250})).find(
          span => span.id === candidate,
        );
        expect(JSON.parse(row?.label?.attrs_json ?? '{}').vs_baseline).toBe(2.5);
      }
    }
  }
});
