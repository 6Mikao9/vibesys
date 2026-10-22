import {expect, test} from 'bun:test';
import {create, toBinary} from '@bufbuild/protobuf';
import {
  DrainGoal,
  LifecycleSchema,
  Outcome,
  RunLiveness,
  StageState,
  StartupStage,
  WatchRunsResponseSchema,
} from '@vibesys/proto';
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

test('property: control availability is a projection of the latest writer lifecycle', () => {
  const lifecycles = [
    create(LifecycleSchema, {
      state: {
        case: 'starting',
        value: {stage: StartupStage.PROBING_WORKSPACE, state: StageState.RUNNING},
      },
    }),
    create(LifecycleSchema, {state: {case: 'running', value: {}}}),
    create(LifecycleSchema, {
      state: {case: 'running', value: {hold: {reason: {case: 'operator', value: {}}}}},
    }),
    create(LifecycleSchema, {state: {case: 'draining', value: {goal: DrainGoal.PARK}}}),
    create(LifecycleSchema, {state: {case: 'parked', value: {}}}),
    ...[Outcome.FINISHED, Outcome.FAILED, Outcome.STOPPED, Outcome.INTERRUPTED].flatMap(outcome =>
      [false, true].map(resumable =>
        create(LifecycleSchema, {state: {case: 'ended', value: {outcome, resumable}}}),
      ),
    ),
  ];
  for (const epoch of [0n, 1n, 3n]) {
    for (const lifecycle of lifecycles) {
      const state = lifecycle.state;
      const observation = {epoch, lifecycle};
      const actions = intents.runIntentActions(observation);
      const live = ['starting', 'running', 'draining'].includes(state.case ?? '') && epoch > 0n;
      expect(actions.includes('pauseNow')).toBe(live);
      expect(actions.includes('pause')).toBe(
        live &&
          state.case !== 'draining' &&
          !(state.case === 'running' && state.value.hold?.reason.case === 'operator'),
      );
      expect(actions.includes('steer')).toBe(epoch > 0n && state.case === 'running');
      const canResume =
        state.case === 'parked' || (state.case === 'ended' && state.value.resumable);
      const values = [RunLiveness.UNSPECIFIED, RunLiveness.ALIVE, RunLiveness.ENDED];
      for (const value of values) {
        expect(intents.relaunchTarget(observation, {epoch, value}) !== null).toBe(
          epoch > 0n && value !== RunLiveness.ALIVE && canResume,
        );
      }
    }
  }
  expect(intents.runIntentActions(undefined)).toEqual([]);
});
