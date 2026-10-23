/**
 * Rewrites the shared declarative run fixtures and publishes the canonical trace scenario as
 * queue-rs-live. Native journey goldens are regenerated separately with
 * `uv run python -m tests.support.journey_fixtures`.
 */
import {mkdirSync, writeFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {type RunFixture, scenarios} from '@vibesys/run-fixtures';
import {journeyFixture} from '../journey-world.js';

const DATA = fileURLToPath(new URL('../../../../libs/run-fixtures/src/data/', import.meta.url));

function write(fixture: RunFixture): void {
  writeFileSync(`${DATA}${fixture.name}.json`, `${JSON.stringify(fixture, null, 2)}\n`);
  process.stdout.write(`wrote ${fixture.name}.json (${fixture.events.length} events)\n`);
}

mkdirSync(DATA, {recursive: true});
for (const fixture of scenarios()) write(fixture);
write({
  ...journeyFixture('trace'),
  name: 'queue-rs-live',
  description: 'A dynamic search over two planning calls, seven ideas, W5 with a turn and a judge.',
});
