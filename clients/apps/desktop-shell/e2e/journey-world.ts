/** Declarative scenarios projected by Python; all journeys use the one gateway. */
import {readFileSync} from 'node:fs';
import {fromBinary} from '@bufbuild/protobuf';
import {ClientReplaySchema} from '@vibesys/proto';
import {parseFixture, type RunFixture} from '@vibesys/run-fixtures';
import {nativeReplayScript} from './native-fixture.js';

export type Journey =
  | 'trace'
  | 'agents'
  | 'prototype'
  | 'lineage10'
  | 'lineage600'
  | 'live'
  | 'empty'
  | 'quota';

function journeyFixture(name: Journey): RunFixture {
  const {lineage: _lineage, ...fixture} = JSON.parse(
    readFileSync(new URL(`./scenarios/${name}.json`, import.meta.url), 'utf8'),
  );
  return parseFixture(fixture);
}

/** A committed native replay, never TypeScript-authored spans or a second gateway Fake. */
export function journeyScript(name: Journey): string {
  const replay = fromBinary(
    ClientReplaySchema,
    readFileSync(
      new URL(`./scenarios/${name === 'live' ? 'live.initial' : name}.pb`, import.meta.url),
    ),
  );
  return nativeReplayScript(journeyFixture(name), replay);
}
