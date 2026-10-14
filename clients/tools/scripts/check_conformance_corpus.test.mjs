import assert from 'node:assert/strict';
import {mkdir, mkdtemp, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {dirname, join, resolve} from 'node:path';
import test from 'node:test';
import {fileURLToPath} from 'node:url';
import {corpusErrors} from './check_conformance_corpus.mjs';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');

const SCHEMA = {
  properties: {
    request: {
      discriminator: {
        mapping: {
          'query.snapshot': '#/$defs/SnapshotQuery',
          'query.history': '#/$defs/HistoryQuery',
        },
      },
    },
  },
  $defs: {
    EventType: {enum: ['run_started', 'run_finished']},
    RunEvent: {
      required: ['type', 'timestamp'],
      properties: {
        protocol_version: {},
        type: {},
        timestamp: {},
        sequence: {},
        run_id: {},
        data: {
          anyOf: [
            {
              discriminator: {
                propertyName: 'kind',
                mapping: {run_started: '#/$defs/RunStartedData'},
              },
            },
            {type: 'null'},
          ],
        },
      },
    },
    Response: {'x-vibesys-response-families': ['snapshot', 'events']},
    SubscribeRequest: {properties: {type: {const: 'subscribe'}}},
    SubscribedMessage: {properties: {type: {const: 'subscribed'}}},
    EventBatchMessage: {properties: {type: {const: 'event_batch'}}},
  },
};

const NO_HELPER_REQUESTS = {
  properties: {request: {discriminator: {mapping: {}}}, message: {discriminator: {mapping: {}}}},
};

const HELPER_SCHEMA = {
  properties: {
    request: {discriminator: {mapping: {hello: '#/$defs/H', ping: '#/$defs/P'}}},
    message: {
      discriminator: {mapping: {hello: '#/$defs/HR', ping: '#/$defs/PR', error: '#/$defs/E'}},
    },
  },
};

function helperScenario(id, steps) {
  return {
    id,
    protocol: 'host-helper',
    title: id,
    description: id,
    role: 'control',
    transports: ['stdio'],
    steps,
  };
}

const CONTRACT = '### WP-ALPHA: first decision\n\ntext\n\n### WP-BETA: second decision\n\ntext\n';

function validEvent(type, data = undefined) {
  const event = {
    protocol_version: 1,
    type,
    sequence: 1,
    run_id: 'r',
    timestamp: '2026-01-01T00:00:00Z',
  };
  if (data !== undefined) event.data = data;
  return event;
}

function validScenario(id, decisions) {
  return {
    id,
    title: id,
    description: id,
    role: 'subscribe',
    transports: ['unix', 'websocket'],
    decisions,
    steps: [
      {dir: 'c2s', frame: {type: 'subscribe', after_sequence: 0}},
      {dir: 's2c', expect: {type: 'subscribed'}},
    ],
  };
}

function runnerInventory(id, scenarios) {
  return {id, description: `${id} runner`, groups: {steps: scenarios}};
}

function renderedDispositions(frontend, kinds = SCHEMA.$defs.EventType.enum) {
  return {
    frontend,
    events: kinds.map(kind => ({kind, disposition: 'rendered', surface: `${kind} surface`})),
    queries: Object.keys(SCHEMA.properties.request.discriminator.mapping).map(kind => ({
      kind,
      disposition: 'used',
      consumer: `${kind} consumer`,
    })),
    responses: SCHEMA.$defs.Response['x-vibesys-response-families'].map(family => ({
      family,
      disposition: 'rendered',
      surface: `${family} surface`,
    })),
    extensions: [
      {
        contract: 'campaign-projection',
        disposition: 'deferred',
        issue: 1761,
        reason: 'The generated campaign projection contract is not present yet.',
      },
    ],
  };
}

function payloadFixtures(schema = SCHEMA) {
  const mapping = schema.$defs.RunEvent.properties.data.anyOf[0].discriminator.mapping;
  return Object.fromEntries(
    Object.keys(mapping).map(kind => [
      `${kind}.jsonl`,
      [validEvent(kind, {kind}), validEvent(kind, {kind, detail: 'fully populated'})],
    ]),
  );
}

function ownerRegistry() {
  return {
    verified_at: '2026-10-14',
    issues: [
      {number: 1761, state: 'open', title: 'Campaign contract', scope: 'Campaign DTO generation'},
      {number: 891, state: 'open', title: 'Web roadmap', scope: 'Web presentation gaps'},
    ],
  };
}

async function writeJsonFiles(directory, files) {
  for (const [name, data] of Object.entries(files)) {
    await writeFile(join(directory, name), JSON.stringify(data));
  }
}

async function writeTree(spec) {
  const root = await mkdtemp(join(tmpdir(), 'vibesys-corpus-'));
  await mkdir(join(root, 'clients', 'libs', 'contracts', 'src', 'generated'), {recursive: true});
  await mkdir(join(root, 'docs', 'contributing'), {recursive: true});
  await mkdir(join(root, 'tests', 'conformance', 'events'), {recursive: true});
  await mkdir(join(root, 'tests', 'conformance', 'event-payloads'), {recursive: true});
  await mkdir(join(root, 'tests', 'conformance', 'rendering-dispositions'), {recursive: true});
  await mkdir(join(root, 'tests', 'conformance', 'scenarios'), {recursive: true});
  await mkdir(join(root, 'tests', 'conformance', 'runners'), {recursive: true});
  await writeFile(
    join(root, 'clients', 'libs', 'contracts', 'src', 'generated', 'protocol.schema.json'),
    JSON.stringify(spec.schema ?? SCHEMA),
  );
  await writeFile(
    join(root, 'clients', 'libs', 'contracts', 'src', 'generated', 'host-helper.schema.json'),
    JSON.stringify(spec.helperSchema ?? NO_HELPER_REQUESTS),
  );
  await writeFile(
    join(root, 'docs', 'contributing', 'wire-protocol.md'),
    spec.contract ?? CONTRACT,
  );
  await writeJsonFiles(join(root, 'tests', 'conformance', 'events'), spec.events ?? {});
  for (const [name, lines] of Object.entries(
    spec.payloads ?? payloadFixtures(spec.schema ?? SCHEMA),
  )) {
    await writeFile(
      join(root, 'tests', 'conformance', 'event-payloads', name),
      `${lines.map(line => JSON.stringify(line)).join('\n')}\n`,
    );
  }
  await writeFile(
    join(root, 'tests', 'conformance', 'deferred-issue-owners.json'),
    JSON.stringify(spec.owners ?? ownerRegistry()),
  );
  const dispositions =
    spec.dispositions ??
    Object.fromEntries(
      ['tui', 'web'].map(frontend => [
        `${frontend}.json`,
        {
          ...renderedDispositions(frontend, (spec.schema ?? SCHEMA).$defs.EventType.enum),
          queries: Object.keys(
            (spec.schema ?? SCHEMA).properties.request.discriminator.mapping,
          ).map(kind => ({kind, disposition: 'used', consumer: `${kind} consumer`})),
          responses: (spec.schema ?? SCHEMA).$defs.Response['x-vibesys-response-families'].map(
            family => ({
              family,
              disposition: 'rendered',
              surface: `${family} surface`,
            }),
          ),
        },
      ]),
    );
  await writeJsonFiles(join(root, 'tests', 'conformance', 'rendering-dispositions'), dispositions);
  await writeJsonFiles(join(root, 'tests', 'conformance', 'scenarios'), spec.scenarios ?? {});
  const scenarioIds = Object.keys(spec.scenarios ?? {}).map(name => name.replace(/\.json$/, ''));
  const runners = spec.runners ?? {'server.json': runnerInventory('server', scenarioIds)};
  await writeJsonFiles(join(root, 'tests', 'conformance', 'runners'), runners);
  return root;
}

function fullEvents() {
  return {
    'run_started.json': validEvent('run_started'),
    'run_finished.json': validEvent('run_finished'),
  };
}

test('the checked-in corpus is complete and well formed', async () => {
  assert.deepEqual(await corpusErrors(REPO_ROOT), []);
});

test('a well-formed temp corpus produces no errors', async () => {
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  assert.deepEqual(await corpusErrors(root), []);
});

test('a multi-client scenario validates named transport actors', async () => {
  const scenario = validScenario('dual', ['WP-ALPHA', 'WP-BETA']);
  scenario.clients = {terminal: {transport: 'unix'}, browser: {transport: 'websocket'}};
  scenario.steps = scenario.steps.map((step, index) => ({
    ...step,
    client: index === 0 ? 'terminal' : 'browser',
  }));
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'dual.json': scenario},
  });
  assert.deepEqual(await corpusErrors(root), []);
});

test('a multi-client step cannot name an undeclared actor', async () => {
  const scenario = validScenario('dual', ['WP-ALPHA', 'WP-BETA']);
  scenario.clients = {terminal: {transport: 'unix'}, browser: {transport: 'websocket'}};
  scenario.steps = scenario.steps.map(step => ({...step, client: 'missing'}));
  const root = await writeTree({events: fullEvents(), scenarios: {'dual.json': scenario}});
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('must name a client declared by the scenario')));
});

test('a missing event fixture fails coverage', async () => {
  const root = await writeTree({
    events: {'run_started.json': validEvent('run_started')},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('EventType "run_finished" has no fixture')));
});

test('a malformed event fixture is rejected', async () => {
  const mismatched = validEvent('run_started');
  const unknownField = {...validEvent('run_finished'), bogus: 1};
  const root = await writeTree({
    events: {'run_started.json': mismatched, 'run_finished.json': unknownField},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('unknown RunEvent field "bogus"')));
});

test('a fixture missing a schema-required field is rejected', async () => {
  const {timestamp: _dropped, ...withoutTimestamp} = validEvent('run_finished');
  const root = await writeTree({
    events: {'run_started.json': validEvent('run_started'), 'run_finished.json': withoutTimestamp},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('missing required field "timestamp"')));
});

test('a new schema event kind needs a disposition in every frontend', async () => {
  const schema = structuredClone(SCHEMA);
  schema.$defs.EventType.enum.push('future_event');
  const dispositions = {
    'tui.json': renderedDispositions('tui'),
    'web.json': renderedDispositions('web'),
  };
  const root = await writeTree({
    schema,
    events: {...fullEvents(), 'future_event.json': validEvent('future_event')},
    dispositions,
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.deepEqual(
    errors.filter(error => error.includes('future_event')),
    [
      'rendering-dispositions/tui.json: EventType "future_event" has no disposition',
      'rendering-dispositions/web.json: EventType "future_event" has no disposition',
    ],
  );
});

test('a new schema query discriminant needs a disposition in every frontend', async () => {
  const schema = structuredClone(SCHEMA);
  schema.properties.request.discriminator.mapping['query.future'] = '#/$defs/FutureQuery';
  const dispositions = {
    'tui.json': renderedDispositions('tui'),
    'web.json': renderedDispositions('web'),
  };
  const root = await writeTree({
    schema,
    events: fullEvents(),
    dispositions,
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.deepEqual(
    errors.filter(error => error.includes('query.future')),
    [
      'rendering-dispositions/tui.json: query discriminant "query.future" has no disposition',
      'rendering-dispositions/web.json: query discriminant "query.future" has no disposition',
    ],
  );
});

test('a new schema response family needs a disposition in every frontend', async () => {
  const schema = structuredClone(SCHEMA);
  schema.$defs.Response['x-vibesys-response-families'].push('future_response');
  const dispositions = {
    'tui.json': renderedDispositions('tui'),
    'web.json': renderedDispositions('web'),
  };
  const root = await writeTree({
    schema,
    events: fullEvents(),
    dispositions,
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.deepEqual(
    errors.filter(error => error.includes('future_response')),
    [
      'rendering-dispositions/tui.json: response family "future_response" has no disposition',
      'rendering-dispositions/web.json: response family "future_response" has no disposition',
    ],
  );
});

test('a new EventData variant needs a payload-rich Python fixture', async () => {
  const schema = structuredClone(SCHEMA);
  schema.$defs.RunEvent.properties.data.anyOf[0].discriminator.mapping.future_data =
    '#/$defs/FutureData';
  const root = await writeTree({
    schema,
    events: fullEvents(),
    payloads: payloadFixtures(),
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.includes('event-payloads/: EventData "future_data" has no fixture'));
});

test('payload fixtures contain exactly default and fully populated typed RunEvents', async () => {
  const root = await writeTree({
    events: fullEvents(),
    payloads: {'run_started.jsonl': [validEvent('run_started', {kind: 'wrong_data'})]},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(
    errors.includes(
      'event-payloads/run_started.jsonl: must contain exactly two RunEvents (defaults and fully populated)',
    ),
  );
});

test('payload fixture data discriminants match their schema-derived filename', async () => {
  const wrong = validEvent('run_started', {kind: 'wrong_data'});
  const root = await writeTree({
    events: fullEvents(),
    payloads: {'run_started.jsonl': [wrong, wrong]},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.deepEqual(
    errors.filter(error => error.includes('data.kind must equal')),
    [
      'event-payloads/run_started.jsonl line 1: data.kind must equal the filename stem "run_started"',
      'event-payloads/run_started.jsonl line 2: data.kind must equal the filename stem "run_started"',
    ],
  );
});

test('disposition tables are required for exactly the supported frontends', async () => {
  const root = await writeTree({
    events: fullEvents(),
    dispositions: {
      'tui.json': renderedDispositions('browser'),
      'electron.json': renderedDispositions('electron'),
    },
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(
    errors.includes(
      'rendering-dispositions/tui.json: "frontend" must equal the filename stem "tui"',
    ),
  );
  assert.ok(errors.includes('rendering-dispositions/web.json: required table is missing'));
  assert.ok(
    errors.includes('rendering-dispositions/electron.json: unknown frontend table "electron"'),
  );
});

test('disposition tables reject unknown and duplicate event kinds', async () => {
  const tui = renderedDispositions('tui');
  tui.events.push({...tui.events[0]});
  tui.events[1] = {...tui.events[1], kind: 'ghost_event'};
  const root = await writeTree({
    events: fullEvents(),
    dispositions: {'tui.json': tui, 'web.json': renderedDispositions('web')},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(
    errors.includes(
      'rendering-dispositions/tui.json events entry 1: kind "ghost_event" is not a member of EventType',
    ),
  );
  assert.ok(
    errors.includes(
      'rendering-dispositions/tui.json events entry 2: EventType "run_started" is listed more than once',
    ),
  );
  assert.ok(
    errors.includes('rendering-dispositions/tui.json: EventType "run_finished" has no disposition'),
  );
});

test('rendered and deferred dispositions have closed status-specific shapes', async () => {
  const tui = renderedDispositions('tui');
  tui.extra = true;
  tui.events = [
    {kind: 'run_started', disposition: 'rendered', surface: '', issue: 834},
    {kind: 'run_finished', disposition: 'deferred', issue: 0, reason: '', surface: 'wrong'},
  ];
  const web = renderedDispositions('web');
  web.events[0] = {kind: 'run_started', disposition: 'unknown', note: 'not closed'};
  const root = await writeTree({
    events: fullEvents(),
    dispositions: {'tui.json': tui, 'web.json': web},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  for (const expected of [
    'rendering-dispositions/tui.json: unknown field "extra"',
    'rendering-dispositions/tui.json events entry 0: unknown field "issue"',
    'rendering-dispositions/tui.json events entry 0: rendered disposition needs a non-empty "surface"',
    'rendering-dispositions/tui.json events entry 1: unknown field "surface"',
    'rendering-dispositions/tui.json events entry 1: deferred disposition needs a positive integer "issue"',
    'rendering-dispositions/tui.json events entry 1: deferred disposition needs a non-empty "reason"',
    'rendering-dispositions/web.json events entry 0: "disposition" must be one of rendered, deferred',
    'rendering-dispositions/web.json events entry 0: unknown field "note"',
  ]) {
    assert.ok(errors.includes(expected), expected);
  }
});

test('query and response dispositions have closed status-specific shapes', async () => {
  const tui = renderedDispositions('tui');
  tui.queries[0] = {
    kind: 'query.snapshot',
    disposition: 'used',
    consumer: '',
    surface: 'wrong',
  };
  tui.responses[0] = {
    family: 'snapshot',
    disposition: 'not_applicable',
    reason: '',
    consumer: 'wrong',
  };
  const root = await writeTree({
    events: fullEvents(),
    dispositions: {'tui.json': tui, 'web.json': renderedDispositions('web')},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  for (const expected of [
    'rendering-dispositions/tui.json queries entry 0: unknown field "surface"',
    'rendering-dispositions/tui.json queries entry 0: used disposition needs a non-empty "consumer"',
    'rendering-dispositions/tui.json responses entry 0: unknown field "consumer"',
    'rendering-dispositions/tui.json responses entry 0: not_applicable disposition needs a non-empty "reason"',
  ]) {
    assert.ok(errors.includes(expected), expected);
  }
});

test('query and response sections reject unknown, duplicate, and invalid statuses', async () => {
  const tui = renderedDispositions('tui');
  tui.queries.push({...tui.queries[0]});
  tui.queries[1] = {...tui.queries[1], kind: 'query.ghost'};
  tui.responses.push({...tui.responses[0]});
  tui.responses[1] = {...tui.responses[1], family: 'ghost_response'};
  const web = renderedDispositions('web');
  web.queries[0] = {kind: 'query.snapshot', disposition: 'rendered', surface: 'wrong'};
  web.responses[0] = {family: 'snapshot', disposition: 'used', consumer: 'wrong'};
  const root = await writeTree({
    events: fullEvents(),
    dispositions: {'tui.json': tui, 'web.json': web},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  for (const expected of [
    'rendering-dispositions/tui.json queries entry 1: kind "query.ghost" is not a member of query discriminant',
    'rendering-dispositions/tui.json queries entry 2: query discriminant "query.snapshot" is listed more than once',
    'rendering-dispositions/tui.json: query discriminant "query.history" has no disposition',
    'rendering-dispositions/tui.json responses entry 1: family "ghost_response" is not a member of response family',
    'rendering-dispositions/tui.json responses entry 2: response family "snapshot" is listed more than once',
    'rendering-dispositions/tui.json: response family "events" has no disposition',
    'rendering-dispositions/web.json queries entry 0: "disposition" must be one of used, deferred, not_applicable',
    'rendering-dispositions/web.json responses entry 0: "disposition" must be one of rendered, deferred, not_applicable',
  ]) {
    assert.ok(errors.includes(expected), expected);
  }
});

test('the campaign extension disposition is mandatory and cannot be silently dropped', async () => {
  const tui = renderedDispositions('tui');
  tui.extensions = [];
  const root = await writeTree({
    events: fullEvents(),
    dispositions: {'tui.json': tui, 'web.json': renderedDispositions('web')},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(
    errors.includes(
      'rendering-dispositions/tui.json: contract extension "campaign-projection" has no disposition',
    ),
  );
});

test('deferred dispositions require a locally verified open and unique issue owner', async () => {
  const owners = ownerRegistry();
  owners.issues.find(issue => issue.number === 891).state = 'closed';
  owners.issues.push({...owners.issues[0]});
  const web = renderedDispositions('web');
  web.queries[0] = {
    kind: 'query.snapshot',
    disposition: 'deferred',
    issue: 891,
    reason: 'Tracked by the Web roadmap.',
  };
  const root = await writeTree({
    events: fullEvents(),
    owners,
    dispositions: {'tui.json': renderedDispositions('tui'), 'web.json': web},
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.includes('deferred-issue-owners.json issue entry 1: "state" must equal "open"'));
  assert.ok(
    errors.includes(
      'deferred-issue-owners.json issue entry 2: issue #1761 is listed more than once',
    ),
  );
  assert.ok(
    errors.includes(
      'rendering-dispositions/web.json queries entry 0: deferred issue #891 is not an open owner in deferred-issue-owners.json',
    ),
  );
});

test('a scenario tagging an unknown decision is rejected', async () => {
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA', 'WP-GHOST'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(
    errors.some(error => error.includes('decision "WP-GHOST" is not in the wire contract')),
  );
});

test('a contract decision no scenario exercises is reported', async () => {
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'boot.json': validScenario('boot', ['WP-ALPHA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('decision "WP-BETA" is exercised by no scenario')));
});

test('a malformed step is rejected', async () => {
  const scenario = validScenario('boot', ['WP-ALPHA', 'WP-BETA']);
  scenario.steps.push({dir: 's2c', expect: {type: 'not_a_real_frame'}});
  const root = await writeTree({events: fullEvents(), scenarios: {'boot.json': scenario}});
  const errors = await corpusErrors(root);
  assert.ok(
    errors.some(error => error.includes('unknown protocol message type "not_a_real_frame"')),
  );
});

test('a scenario id that disagrees with its filename is rejected', async () => {
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'boot.json': validScenario('mismatch', ['WP-ALPHA', 'WP-BETA'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('"id" must equal the filename stem')));
});

test('every scenario is either runner-executed or declares its required setup, exclusively', async () => {
  const cases = [
    {
      executed: false,
      declaresSetup: false,
      expected: [
        'scenarios/boot.json: scenario must be runner-executed or declare "required_setup"',
      ],
    },
    {executed: false, declaresSetup: true, expected: []},
    {executed: true, declaresSetup: false, expected: []},
    {
      executed: true,
      declaresSetup: true,
      expected: ['scenarios/boot.json: runner-executed scenario must not declare "required_setup"'],
    },
  ];
  for (const {executed, declaresSetup, expected} of cases) {
    const scenario = validScenario('boot', ['WP-ALPHA', 'WP-BETA']);
    if (declaresSetup) scenario.required_setup = ['subscription-redial'];
    const root = await writeTree({
      events: fullEvents(),
      scenarios: {'boot.json': scenario},
      runners: executed ? {'server.json': runnerInventory('server', ['boot'])} : {},
    });
    const errors = await corpusErrors(root);
    const partitionErrors = errors.filter(
      error => error.includes('runner') || error.includes('required_setup'),
    );
    assert.deepEqual(
      partitionErrors,
      expected,
      `executed=${executed}, declaresSetup=${declaresSetup}`,
    );
  }
});

test('required setup names are non-empty capability tokens', async () => {
  const scenario = validScenario('boot', ['WP-ALPHA', 'WP-BETA']);
  scenario.required_setup = ['subscription-redial', 'not a token', 'subscription-redial'];
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'boot.json': scenario},
    runners: {},
  });
  const errors = await corpusErrors(root);
  assert.ok(
    errors.some(error => error.includes('must contain unique kebab-case capability names')),
  );
});

test('a runner cannot register a scenario the corpus does not contain', async () => {
  const scenario = validScenario('boot', ['WP-ALPHA', 'WP-BETA']);
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'boot.json': scenario},
    runners: {'server.json': runnerInventory('server', ['boot', 'missing'])},
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('unknown scenario "missing"')));
});

test('runner inventories reject empty metadata, groups, and duplicate registration', async () => {
  const scenario = validScenario('boot', ['WP-ALPHA', 'WP-BETA']);
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {'boot.json': scenario},
    runners: {
      'server.json': {
        id: 'server',
        description: '',
        extra: true,
        groups: {first: ['boot'], second: ['boot'], 'bad group': []},
      },
    },
  });
  const errors = await corpusErrors(root);
  assert.ok(errors.some(error => error.includes('"description" must be a non-empty string')));
  assert.ok(errors.some(error => error.includes('unknown runner field "extra"')));
  assert.ok(errors.some(error => error.includes('registered in more than one group')));
  assert.ok(errors.some(error => error.includes('group name "bad group" must be kebab-case')));
  assert.ok(
    errors.some(error => error.includes('group "bad group" must be a non-empty scenario array')),
  );
});

test('host helper scenarios are checked against the helper schema and must send every request', async () => {
  const handshake = helperScenario('helper-handshake', [
    {dir: 'c2s', frame: {type: 'hello', request_id: 'h'}},
    {dir: 's2c', expect: {type: 'hello', request_id: 'h'}},
  ]);
  const scenarios = {
    'boot.json': validScenario('boot', ['WP-ALPHA', 'WP-BETA']),
    'helper-handshake.json': handshake,
  };
  const ok = await writeTree({events: fullEvents(), scenarios, helperSchema: HELPER_SCHEMA});
  const errors = await corpusErrors(ok);
  assert.deepEqual(errors, [
    'host-helper.schema.json: request "ping" is sent by no host helper scenario',
  ]);

  const broken = helperScenario('helper-broken', [
    {dir: 'c2s', frame: {type: 'pong', request_id: 'p'}},
    {dir: 's2c', expect: {type: 'subscribe'}},
  ]);
  broken.decisions = ['WP-ALPHA'];
  broken.role = 'subscribe';
  const root = await writeTree({
    events: fullEvents(),
    scenarios: {...scenarios, 'helper-broken.json': broken},
    helperSchema: HELPER_SCHEMA,
  });
  const found = await corpusErrors(root);
  assert.ok(found.some(error => error.includes('unknown host helper request type "pong"')));
  assert.ok(found.some(error => error.includes('unknown host helper message type "subscribe"')));
  assert.ok(found.some(error => error.includes('a host helper scenario has no "decisions"')));
  assert.ok(found.some(error => error.includes('"role" must be one of control, run')));
});
