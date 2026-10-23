import {expect, test} from 'bun:test';
import {FakeClock} from '@vibesys/transport/testing';
import {Schedule} from './schedule.js';

test('a world with nothing left to deliver or fire fails with its seed instead of waiting', async () => {
  const schedule = new Schedule(23);
  await expect(schedule.run(new Promise(() => {}))).rejects.toThrow('seed 23: stalled');
});

test('an endless retry is cut at the event bound and reported as busy', async () => {
  const clock = new FakeClock();
  const schedule = new Schedule(7, clock);
  const retry = (): void => {
    clock.schedule(retry, 1000);
  };
  retry();
  expect(await schedule.quiesce(40)).toBe(false);
  expect(schedule.events).toBe(40);
  await expect(schedule.until(() => false, 10)).rejects.toThrow('seed 7: exceeded 10');
});

test('a finite chain of deliveries and timers ends quiet, in every seeded order', async () => {
  for (let seed = 1; seed <= 50; seed += 1) {
    const clock = new FakeClock();
    const schedule = new Schedule(seed, clock);
    const seen: string[] = [];
    const first = {};
    const second = {};
    schedule.post(first, () => {
      seen.push('a1');
      clock.schedule(() => seen.push('timer'), 500);
    });
    schedule.post(first, () => seen.push('a2'));
    schedule.post(second, () => seen.push('b1'));
    expect(await schedule.quiesce()).toBe(true);
    expect(seen.filter(entry => entry.startsWith('a'))).toEqual(['a1', 'a2']);
    expect(seen.at(-1)).toBe('timer');
    expect(seen).toHaveLength(4);
  }
});
