import {describe, expect, test} from 'bun:test';
import {CODE_IDS} from '@vibesys/contracts';
import {stepLabel, stepTitle} from './names.js';

/** The roles the backend declares, as a step names them in a trace: by id, and by its own title. */
describe('how a step is named for a person', () => {
  test('an agent turn titled by its role id reads as the role’s name, whatever the role', () => {
    for (const role of CODE_IDS.roles) {
      const step = {role, title: role};
      expect(stepTitle(step)).not.toBe(role);
      expect(stepTitle(step)).toMatch(/^[A-Z]/);
      expect(stepLabel(step)).toBe(stepTitle(step));
    }
  });

  test('a step with its own headline keeps it, and its role is still named', () => {
    const step = {role: 'implementer', title: 'Pad the counters'};
    expect(stepTitle(step)).toBe('Pad the counters');
    expect(stepLabel(step)).toBe('Implementer');
  });

  test('a step without a role is named by its title', () => {
    expect(stepLabel({role: null, title: 'correctness + benchmark'})).toBe(
      'correctness + benchmark',
    );
  });
});
