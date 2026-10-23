import {expect, test} from 'bun:test';
import {create, toBinary} from '@bufbuild/protobuf';
import {LifecycleSchema, Outcome, RunLiveness, WatchRunsResponseSchema} from '@vibesys/proto';
import * as intents from './index.js';

test('every ended observation offers Resume while a live writer never does', () => {
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
    // The host admits Resume after every ending; the flag it keeps for older readers is ignored.
    for (const outcome of [Outcome.FINISHED, Outcome.FAILED])
      for (const resumable of [true, false]) {
        const lifecycle = create(LifecycleSchema, {
          state: {case: 'ended', value: {outcome, resumable}},
        });
        for (const value of [RunLiveness.ENDED, RunLiveness.INTERRUPTED, RunLiveness.UNSPECIFIED])
          expect(
            intents.relaunchTarget({epoch: observed, lifecycle}, {epoch: observed, value}),
          ).toEqual({epoch: observed, state: 'ended'});
        expect(
          intents.relaunchTarget(
            {epoch: observed, lifecycle},
            {epoch: observed, value: RunLiveness.ALIVE},
          ),
        ).toBeNull();
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
    head: {epoch: 3, process: {epoch: 9}},
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

test('process metadata never supplies a missing canonical epoch', () => {
  for (const epoch of [1, 7, 4294967295]) {
    const response = create(WatchRunsResponseSchema, {
      runId: 'run-a',
      head: {process: {epoch}},
      liveness: RunLiveness.INTERRUPTED,
    });
    expect(
      intents.livenessResponse(toBinary(WatchRunsResponseSchema, response), 'run-a')?.epoch,
    ).toBe(0n);
  }
});
