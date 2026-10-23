/** Native fixture data comes from the shipping pure projection, once per content hash. */
import {execFileSync} from 'node:child_process';
import {createHash} from 'node:crypto';
import {existsSync, readdirSync, readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {create, fromBinary, toBinary} from '@bufbuild/protobuf';
import {
  API_SCHEMA_HASH,
  ClientReplaySchema,
  RecordSchema,
  RunLiveness,
  STORE_SCHEMA_HASH,
  TailResponseSchema,
} from '@vibesys/proto';
import type {RunFixture, StoreRunFixture} from '@vibesys/run-fixtures';
import {gatewayScript, wire} from './fake-gateway.js';

const root = fileURLToPath(new URL('../../../../', import.meta.url));
const projector = join(root, 'tests/e2e/retained_client_fixture.py');
const cache = join(
  tmpdir(),
  `vibesys-native-fixtures-${hash(
    [
      API_SCHEMA_HASH,
      STORE_SCHEMA_HASH,
      readFileSync(projector).toString(),
      readFileSync(join(root, 'tests/support/retained_events.py')).toString(),
      ...files(join(root, 'src/vibesys/records'))
        .filter(path => path.endsWith('.py'))
        .sort()
        .map(path => readFileSync(path).toString()),
    ].join('\n'),
  )}`,
);
function hash(bytes: string | Uint8Array): string {
  return createHash('sha256').update(bytes).digest('hex');
}
function files(root: string): string[] {
  if (!existsSync(root)) return [];
  return readdirSync(root, {recursive: true, withFileTypes: true})
    .filter(entry => entry.isFile())
    .map(entry => join(entry.parentPath, entry.name));
}
function ensure(fixtures: readonly RunFixture[]): void {
  const missing = fixtures
    .map(fixture => ({key: hash(JSON.stringify(fixture)), fixture}))
    .filter(item => !existsSync(join(cache, item.key, '.complete')));
  if (missing.length === 0) return;
  execFileSync('uv', ['run', '--locked', '--project', root, 'python', projector, cache], {
    input: JSON.stringify(missing),
    maxBuffer: 16 << 20,
    timeout: 120000,
  });
}
export function nativeFixtureScript(fixture: RunFixture): string {
  ensure([fixture]);
  const directory = join(cache, hash(JSON.stringify(fixture)));
  const replay = fromBinary(ClientReplaySchema, readFileSync(join(directory, 'replay.pb')));
  const groups = new Map(
    replay.batches.map(batch => [
      Number(batch.sourceSequence),
      batch.records.map(bytes => fromBinary(RecordSchema, bytes)),
    ]),
  );
  const through = fixture.state === 'live' ? (fixture.liveAfter ?? Infinity) : Infinity;
  const records = [...groups]
    .filter(([source]) => source <= through)
    .flatMap(([, rows]) => rows)
    .sort((a, b) => Number(a.seq - b.seq));
  const stored: StoreRunFixture = {
    records,
    initialCount: records.length,
    blobs: replay.blobs,
    streams: replay.streams,
  };
  const input = wire(stored, fixture.state === 'live' ? RunLiveness.ALIVE : RunLiveness.ENDED);
  const updates = Object.fromEntries(
    [...groups]
      .filter(([source]) => source > through)
      .map(([source, rows]) => [
        source,
        [
          ...toBinary(
            TailResponseSchema,
            create(TailResponseSchema, {
              records: rows
                .sort((a, b) => Number(a.seq - b.seq))
                .map(row => toBinary(RecordSchema, row)),
              throughSeq: rows.at(-1)?.seq ?? 0n,
            }),
          ),
        ],
      ]),
  );
  return `if (!window.vibesysGateway) {${gatewayScript(input)}\n(${connectUpdates.toString()})(${JSON.stringify(updates)});}`;
}
function connectUpdates(updates: Record<string, number[]>): void {
  const peer = window as unknown as {
    fakeGateway: {append(bytes: number[]): void; disconnect(): void};
    fakeHost: {emit(events: readonly {sequence?: number}[]): void; drop(): void};
  };
  const emit = peer.fakeHost.emit;
  peer.fakeHost.emit = events => {
    emit(events);
    for (const event of events) {
      const bytes = updates[String(event.sequence)];
      if (bytes) peer.fakeGateway.append(bytes);
    }
  };
  const drop = peer.fakeHost.drop;
  peer.fakeHost.drop = () => {
    drop();
    peer.fakeGateway.disconnect();
  };
}
