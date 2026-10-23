/** Declared metric points join existing measurements without strategy decisions. */
import type {Span} from '@vibesys/contracts';
import {Direction} from '@vibesys/proto';
import type {StoreView} from './types.js';

interface Reading {
  name: string;
  value: number;
  unit: string;
  direction: 'max' | 'min';
}
function readingsBySpan(view: StoreView): ReadonlyMap<string, readonly Reading[]> {
  const points = new Map<string, Map<string, Reading>>();
  for (const history of view.metrics)
    for (const version of history.points) {
      const point = version.point;
      if (!point?.span) continue;
      const epoch = view.epochs.filter(item => item.seq <= version.seq).at(-1)?.epoch;
      const declaration = epoch?.series.find(item => item.name === history.series);
      if (
        !declaration ||
        (declaration.direction !== Direction.MAX && declaration.direction !== Direction.MIN)
      )
        continue;
      const rows = points.get(point.span) ?? new Map<string, Reading>();
      rows.set(history.series, {
        name: history.series,
        value: point.value,
        unit: declaration.unit,
        direction: declaration.direction === Direction.MAX ? 'max' : 'min',
      });
      points.set(point.span, rows);
    }
  return new Map([...points].map(([span, rows]) => [span, [...rows.values()]]));
}

/** The real input gate's readings and its value of the objective metric, whether or not any idea was measured. */
function inputOf(spans: readonly Span[], view: StoreView) {
  const bySpan = readingsBySpan(view);
  const input = spans
    .filter(
      span =>
        span.label?.kind === 'gate' &&
        (JSON.parse(span.label.attrs_json ?? '{}') as Record<string, unknown>)['subject'] ===
          'GATE_SUBJECT_INPUT',
    )
    .at(-1);
  const readings = input ? (bySpan.get(input.id) ?? []) : [];
  const metric = view.epochs.at(-1)?.epoch?.run?.objectives[0]?.series;
  return {bySpan, readings, metric, value: readings.find(item => item.name === metric)?.value};
}

/** The input's measured value of the objective metric, or null before it is measured. */
export function inputMeasurement(spans: readonly Span[], view: StoreView): number | null {
  return inputOf(spans, view).value ?? null;
}

export function measuredSpans(spans: readonly Span[], view: StoreView): Span[] {
  const {bySpan, readings: inputReadings, metric, value: inputValue} = inputOf(spans, view);
  return spans.map(span => {
    if (span.label?.kind !== 'gate') return span;
    const rows = bySpan.get(span.id) ?? [];
    const value = rows.find(item => item.name === metric)?.value;
    const attrs = JSON.parse(span.label.attrs_json ?? '{}') as Record<string, unknown>;
    return {
      ...span,
      label: {
        ...span.label,
        attrs_json: JSON.stringify({
          ...attrs,
          stage:
            typeof attrs['stage'] === 'string'
              ? attrs['stage'].replace('GATE_STAGE_', '').toLowerCase()
              : undefined,
          metrics: rows,
          input_metrics: inputReadings,
          metric_value: value,
          vs_baseline:
            value !== undefined && inputValue !== undefined && inputValue !== 0
              ? value / inputValue
              : undefined,
        }),
      },
    };
  });
}
