import {expect, test} from 'bun:test';
import {create, toBinary} from '@bufbuild/protobuf';
import {LifecycleSchema, Outcome, RunLiveness, WatchRunsResponseSchema} from '@vibesys/proto';
import * as intents from './index.js';

test('only resumable ended observations offer Resume while a live writer never does', () => {
  for (let epoch = 1; epoch <= 5; epoch += 1) {
    const observed = BigInt(epoch);
    for (const [state, lifecycle] of [
      ['parked', create(LifecycleSchema, {state: {case: 'parked', value: {}}})],
      [
        'ended',
        create(LifecycleSchema, {
          state: {case: 'ended', value: {outcome: Outcome.STOPPED, resumable: true}},
        }),
      ],
      [
        'ended',
        create(LifecycleSchema, {
          state: {case: 'ended', value: {outcome: Outcome.INTERRUPTED, resumable: true}},
        }),
      ],
    ] as const) {
      expect(
        intents.relaunchTarget(
          {epoch: observed, lifecycle},
          {epoch: observed, value: RunLiveness.ENDED},
        ),
      ).toEqual({epoch: observed, state});
      expect(
        intents.relaunchTarget(
          {epoch: observed, lifecycle},
          {epoch: observed, value: RunLiveness.ALIVE},
        ),
      ).toBeNull();
    }
    for (const outcome of [Outcome.FINISHED, Outcome.FAILED]) {
      const lifecycle = create(LifecycleSchema, {state: {case: 'ended', value: {outcome}}});
      for (const value of [RunLiveness.ALIVE, RunLiveness.ENDED, RunLiveness.INTERRUPTED])
        expect(
          intents.relaunchTarget({epoch: observed, lifecycle}, {epoch: observed, value}),
        ).toEqual(null);
    }
    const lifecycle = create(LifecycleSchema, {state: {case: 'running', value: {}}});
    expect(
      intents.relaunchTarget(
        {epoch: observed, lifecycle},
        {epoch: observed, value: RunLiveness.INTERRUPTED},
      ),
    ).toEqual({epoch: observed, state: 'interrupted'});
    expect(
      intents.relaunchTarget(
        {epoch: observed, lifecycle},
        {epoch: observed - 1n, value: RunLiveness.INTERRUPTED},
      ),
    ).toBeNull();
    expect(
      intents.relaunchTarget(
        {epoch: observed, lifecycle},
        {epoch: observed, value: RunLiveness.UNSPECIFIED},
      ),
    ).toBeNull();
  }
});

test('WatchRuns liveness belongs to its run and writer epoch; removal clears it', () => {
  const response = create(WatchRunsResponseSchema, {
    runId: 'run-a',
    liveness: RunLiveness.INTERRUPTED,
    head: {process: {epoch: 3}},
  });
  expect(intents.livenessResponse(toBinary(WatchRunsResponseSchema, response), 'run-b')).toBeNull();
  expect(intents.livenessResponse(toBinary(WatchRunsResponseSchema, response), 'run-a')).toEqual({
    epoch: 3n,
    value: RunLiveness.INTERRUPTED,
  });
  response.removed = true;
  expect(intents.livenessResponse(toBinary(WatchRunsResponseSchema, response), 'run-a')).toEqual({
    epoch: 3n,
    value: RunLiveness.UNSPECIFIED,
  });
});

test('a newer host epoch fences an older parked snapshot', () => {
  const lifecycle = create(LifecycleSchema, {state: {case: 'parked', value: {}}});
  for (const value of [RunLiveness.ALIVE, RunLiveness.INTERRUPTED, RunLiveness.ENDED]) {
    expect(intents.relaunchTarget({epoch: 1n, lifecycle}, {epoch: 2n, value})).toBeNull();
  }
});
