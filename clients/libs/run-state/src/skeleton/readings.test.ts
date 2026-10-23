/** The input's value is the run's stated fact: no order or count of gates changes it. */
import {expect, test} from 'bun:test';
import {create} from '@bufbuild/protobuf';
import type {Span} from '@vibesys/contracts';
import {
  Direction,
  EpochStartedSchema,
  EpochVersionSchema,
  InputMeasurementSchema,
  MeasuredInputSchema,
  MetricHistorySchema,
  MetricPointSchema,
  MetricSeriesDeclSchema,
  MetricVersionSchema,
  ObjectiveSchema,
  RunDescriptionSchema,
  UnmeasuredInputSchema,
} from '@vibesys/proto';
import {inputMeasurement, measuredSpans} from './readings.js';
import type {StoreView} from './types.js';

type Stated = 'absent' | 'unmeasured' | number;

const gate = (id: string, subject: string): Span =>
  ({
    id,
    label: {kind: 'gate', attrs_json: JSON.stringify({subject, stage: 'GATE_STAGE_BENCHMARK'})},
  }) as unknown as Span;

function view(measured: Record<string, number>, stated: Stated): StoreView {
  const inputMeasurementField =
    stated === 'absent'
      ? {}
      : {
          inputMeasurement: create(InputMeasurementSchema, {
            state:
              stated === 'unmeasured'
                ? {case: 'unmeasured', value: create(UnmeasuredInputSchema, {})}
                : {
                    case: 'measured',
                    value: create(MeasuredInputSchema, {
                      series: 'speed',
                      value: stated,
                      span: 'gate:input:0',
                    }),
                  },
          }),
        };
  const epoch = create(EpochStartedSchema, {
    run: create(RunDescriptionSchema, {
      objectives: [create(ObjectiveSchema, {series: 'speed', direction: Direction.MAX})],
      ...inputMeasurementField,
    }),
    series: [
      create(MetricSeriesDeclSchema, {name: 'speed', unit: 'ops', direction: Direction.MAX}),
    ],
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

test('every surface shows the stated input, whatever the gates and their order', () => {
  const candidate = 'gate:vs-a:benchmark';
  for (const count of [0, 1, 2, 3]) {
    const gates = Array.from({length: count}, (_, at) => `gate:input:${at}`);
    for (const order of orderings([...gates, candidate])) {
      const spans = order.map(id =>
        gate(id, id === candidate ? 'GATE_SUBJECT_CANDIDATE' : 'GATE_SUBJECT_INPUT'),
      );
      // Readings sit on input gates that the fact does not name; none of them is the input.
      const readings = {...Object.fromEntries(gates.map(id => [id, 7])), [candidate]: 250};
      const stated = view(readings, 100);
      const row = measuredSpans(spans, stated).find(span => span.id === candidate);
      expect(JSON.parse(row?.label?.attrs_json ?? '{}').vs_baseline).toBe(2.5);
      expect(inputMeasurement(stated)).toBe(100);
    }
  }
});

test('an unmeasured input has no ratio, and a run without the field uses its head', () => {
  const spans = [gate('gate:input:0', 'GATE_SUBJECT_INPUT'), gate('c', 'GATE_SUBJECT_CANDIDATE')];
  const unmeasured = view({c: 250}, 'unmeasured');
  expect(inputMeasurement(unmeasured, 40)).toBeNull();
  const row = measuredSpans(spans, unmeasured, 40).find(span => span.id === 'c');
  expect(JSON.parse(row?.label?.attrs_json ?? '{}').vs_baseline).toBeUndefined();
  const older = view({c: 250}, 'absent');
  expect(inputMeasurement(older)).toBeNull();
  expect(inputMeasurement(older, 100)).toBe(100);
  const older2 = measuredSpans(spans, older, 100).find(span => span.id === 'c');
  expect(JSON.parse(older2?.label?.attrs_json ?? '{}').vs_baseline).toBe(2.5);
});
