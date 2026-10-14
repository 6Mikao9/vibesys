#!/usr/bin/env node

// Validates the shared transport conformance corpus at `<repo>/tests/conformance` against the
// generated protocol schema and the wire contract. Dependency-free on purpose: the workspace pins
// only biome, dependency-cruiser, and knip, so this check hand-rolls the structural validation it
// needs rather than pulling in a JSON-schema library. Message shape is not hand-encoded here: the
// event envelope (its fields and which are required) is read from the generated schema, the single
// source protocol codegen owns (see #850). Host helper scenarios (`"protocol": "host-helper"`) are
// checked against the generated helper schema instead, and every helper request must be sent by one.
// TUI and Web rendering dispositions are exhaustive over that same schema-derived event-kind set.
// See tests/conformance/README.md and FORMAT.md.

import {readdir, readFile} from 'node:fs/promises';
import {basename, dirname, join, resolve} from 'node:path';
import {fileURLToPath} from 'node:url';

const SCHEMA_RELATIVE = 'libs/contracts/src/generated/protocol.schema.json';
const HELPER_SCHEMA_RELATIVE = 'libs/contracts/src/generated/host-helper.schema.json';
const HELPER_PROTOCOL = 'host-helper';
const HELPER_ROLES = new Set(['control', 'run']);
const HELPER_TRANSPORTS = new Set(['stdio']);
const CONTRACT_RELATIVE = 'docs/contributing/wire-protocol.md';
const CORPUS_RELATIVE = 'tests/conformance';
const DISPOSITIONS_RELATIVE = 'rendering-dispositions';
const PAYLOADS_RELATIVE = 'event-payloads';
const ISSUE_OWNERS_RELATIVE = 'deferred-issue-owners.json';
const RUNNERS_RELATIVE = 'runners';
const FRONTENDS = ['tui', 'web'];
const CONTRACT_EXTENSIONS = new Set(['campaign-projection']);

const STEP_DIRECTIONS = new Set(['c2s', 's2c']);
const SCENARIO_ROLES = new Set(['control', 'subscribe', 'chat']);
const KNOWN_TRANSPORTS = new Set(['unix', 'websocket']);
const RUNNER_FIELDS = new Set(['id', 'description', 'groups']);
const DISPOSITION_TABLE_FIELDS = new Set([
  'frontend',
  'events',
  'queries',
  'responses',
  'extensions',
]);
const ISSUE_OWNER_FIELDS = new Set(['number', 'state', 'title', 'scope']);
const ISSUE_OWNERS_FIELDS = new Set(['verified_at', 'issues']);
const SETUP_CAPABILITY = /^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$/;
// A control-path reply is a `Response`, which has no `type` discriminant, so scenarios name it with
// this pseudo-type. Every other frame type is derived from the schema. The name is not defined
// here: it is the section the generated schema publishes `Response` under, derived in
// `tests/conformance/frame_matching.py`. This gate runs in its own process and cannot import that,
// so it restates the literal and `test_frame_matching.py` fails if the two ever disagree.
const RESPONSE_PSEUDO_TYPE = 'response';

function typeConsts(schema) {
  const consts = new Set();
  for (const def of Object.values(schema.$defs ?? {})) {
    const value = def?.properties?.type?.const;
    if (typeof value === 'string') consts.add(value);
  }
  return consts;
}

/** The `EventType` enum: the authoritative event-kind set the corpus must cover one-for-one. */
function eventKinds(schema) {
  return new Set(schema.$defs?.EventType?.enum ?? []);
}

/** Every query request tag, from the generated request union rather than frontend call sites. */
function queryKinds(schema) {
  return new Set(
    Object.keys(schema.properties?.request?.discriminator?.mapping ?? {}).filter(kind =>
      kind.startsWith('query.'),
    ),
  );
}

/** Operation payload slots explicitly classified by the authoritative Python response model. */
function responseFamilies(schema) {
  return new Set(schema.$defs?.Response?.['x-vibesys-response-families'] ?? []);
}

/** Every tagged EventData member reachable through RunEvent.data. */
function eventDataKinds(schema) {
  const choices = schema.$defs?.RunEvent?.properties?.data?.anyOf ?? [];
  const tagged = choices.find(choice => choice?.discriminator?.propertyName === 'kind');
  return new Set(Object.keys(tagged?.discriminator?.mapping ?? {}));
}

/** The `RunEvent` envelope: which top-level fields an event fixture may carry. */
function runEventFields(schema) {
  return new Set(Object.keys(schema.$defs?.RunEvent?.properties ?? {}));
}

/** The `RunEvent` required fields, read from the schema rather than hand-encoded. */
function runEventRequiredFields(schema) {
  return schema.$defs?.RunEvent?.required ?? [];
}

/** Valid frame types a scenario may name: every discriminant const that is not an event kind. */
function frameTypes(schema) {
  const kinds = eventKinds(schema);
  const frames = new Set([RESPONSE_PSEUDO_TYPE]);
  for (const value of typeConsts(schema)) {
    if (!kinds.has(value)) frames.add(value);
  }
  return frames;
}

/** The helper's request and message tags, read from the generated helper schema's tagged unions. */
function helperTypes(schema) {
  const tags = section =>
    new Set(Object.keys(schema.properties?.[section]?.discriminator?.mapping ?? {}));
  return {requests: tags('request'), messages: tags('message')};
}

/** Decision tokens declared as `### WP-...:` headings in the wire contract. */
function contractDecisions(markdown) {
  const decisions = new Set();
  const pattern = /^###\s+(WP-[A-Z-]+):/gm;
  let match = pattern.exec(markdown);
  while (match !== null) {
    decisions.add(match[1]);
    match = pattern.exec(markdown);
  }
  return decisions;
}

async function readJsonDir(dir) {
  let entries;
  try {
    entries = await readdir(dir);
  } catch (error) {
    return {files: [], error: `cannot read ${dir}: ${error.message}`};
  }
  const files = [];
  for (const name of entries.filter(entry => entry.endsWith('.json')).sort()) {
    const stem = basename(name, '.json');
    try {
      files.push({name, stem, data: JSON.parse(await readFile(join(dir, name), 'utf8'))});
    } catch (error) {
      files.push({name, stem, parseError: error.message});
    }
  }
  return {files, error: null};
}

async function readJsonlDir(dir) {
  let entries;
  try {
    entries = await readdir(dir);
  } catch (error) {
    return {files: [], error: `cannot read ${dir}: ${error.message}`};
  }
  const files = [];
  for (const name of entries.filter(entry => entry.endsWith('.jsonl')).sort()) {
    const stem = basename(name, '.jsonl');
    try {
      const lines = (await readFile(join(dir, name), 'utf8')).trimEnd().split('\n');
      files.push({name, stem, data: lines.map(line => JSON.parse(line))});
    } catch (error) {
      files.push({name, stem, parseError: error.message});
    }
  }
  return {files, error: null};
}

function checkEventFixture(fixture, kinds, allowedFields, requiredFields) {
  const errors = [];
  const label = `events/${fixture.name}`;
  if (fixture.parseError) return [`${label}: invalid JSON: ${fixture.parseError}`];
  const event = fixture.data;
  if (typeof event?.type !== 'string') return [`${label}: missing string field "type"`];
  if (event.type !== fixture.stem) {
    errors.push(`${label}: filename stem "${fixture.stem}" does not match type "${event.type}"`);
  }
  if (!kinds.has(event.type)) {
    errors.push(`${label}: type "${event.type}" is not a member of the EventType enum`);
  }
  for (const field of requiredFields) {
    if (!(field in event)) errors.push(`${label}: missing required field "${field}"`);
  }
  for (const field of Object.keys(event)) {
    if (!allowedFields.has(field)) errors.push(`${label}: unknown RunEvent field "${field}"`);
  }
  return errors;
}

function checkEventCoverage(files, kinds) {
  const errors = [];
  const present = new Set(files.filter(file => !file.parseError).map(file => file.stem));
  for (const kind of [...kinds].sort()) {
    if (!present.has(kind)) errors.push(`events/: EventType "${kind}" has no fixture`);
  }
  return errors;
}

function checkPayloadEvent(event, where, stem, kinds, allowedFields, requiredFields) {
  const errors = [];
  const shapeErrors = objectErrors(event, where);
  if (shapeErrors.length > 0) return shapeErrors;
  if (typeof event.type !== 'string' || !kinds.has(event.type)) {
    errors.push(`${where}: "type" must be a member of EventType`);
  }
  for (const field of requiredFields) {
    if (!(field in event)) errors.push(`${where}: missing required field "${field}"`);
  }
  for (const field of Object.keys(event)) {
    if (!allowedFields.has(field)) errors.push(`${where}: unknown RunEvent field "${field}"`);
  }
  if (event.data?.kind !== stem) {
    errors.push(`${where}: data.kind must equal the filename stem "${stem}"`);
  }
  return errors;
}

function checkPayloadFixture(fixture, kinds, allowedFields, requiredFields) {
  const label = `${PAYLOADS_RELATIVE}/${fixture.name}`;
  if (fixture.parseError) return [`${label}: invalid JSONL: ${fixture.parseError}`];
  if (fixture.data.length !== 2) {
    return [`${label}: must contain exactly two RunEvents (defaults and fully populated)`];
  }
  return fixture.data.flatMap((event, index) =>
    checkPayloadEvent(
      event,
      `${label} line ${index + 1}`,
      fixture.stem,
      kinds,
      allowedFields,
      requiredFields,
    ),
  );
}

function checkPayloadCoverage(files, kinds) {
  const present = new Set(files.filter(file => !file.parseError).map(file => file.stem));
  const errors = [];
  for (const kind of [...kinds].sort()) {
    if (!present.has(kind))
      errors.push(`${PAYLOADS_RELATIVE}/: EventData "${kind}" has no fixture`);
  }
  for (const kind of [...present].sort()) {
    if (!kinds.has(kind)) errors.push(`${PAYLOADS_RELATIVE}/: unknown EventData fixture "${kind}"`);
  }
  return errors;
}

function objectErrors(value, label) {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) {
    return [`${label}: must be an object`];
  }
  return [];
}

function unknownFieldErrors(value, allowed, label) {
  return Object.keys(value).flatMap(field =>
    allowed.has(field) ? [] : [`${label}: unknown field "${field}"`],
  );
}

function dispositionKeyErrors(entry, where, spec, seen) {
  const errors = [];
  const value = entry[spec.key];
  if (typeof value !== 'string' || value.length === 0) {
    errors.push(`${where}: "${spec.key}" must be a non-empty string`);
    return errors;
  }
  if (!spec.values.has(value)) {
    errors.push(`${where}: ${spec.key} "${value}" is not a member of ${spec.label}`);
  }
  if (seen.has(value)) {
    errors.push(`${where}: ${spec.label} "${value}" is listed more than once`);
  }
  seen.add(value);
  return errors;
}

function namedDispositionErrors(entry, where, key, detail, description) {
  const errors = unknownFieldErrors(entry, new Set([key, 'disposition', detail]), where);
  if (typeof entry[detail] !== 'string' || entry[detail].trim() === '') {
    errors.push(`${where}: ${description} needs a non-empty "${detail}"`);
  }
  return errors;
}

function deferredDispositionErrors(entry, where, key, openIssues) {
  const errors = unknownFieldErrors(entry, new Set([key, 'disposition', 'issue', 'reason']), where);
  if (!Number.isInteger(entry.issue) || entry.issue <= 0) {
    errors.push(`${where}: deferred disposition needs a positive integer "issue"`);
  } else if (!openIssues.has(entry.issue)) {
    errors.push(
      `${where}: deferred issue #${entry.issue} is not an open owner in ${ISSUE_OWNERS_RELATIVE}`,
    );
  }
  if (typeof entry.reason !== 'string' || entry.reason.trim() === '') {
    errors.push(`${where}: deferred disposition needs a non-empty "reason"`);
  }
  return errors;
}

function dispositionShapeErrors(entry, where, spec, openIssues) {
  if (!spec.statuses.has(entry.disposition)) {
    return [
      `${where}: "disposition" must be one of ${[...spec.statuses].join(', ')}`,
      ...unknownFieldErrors(entry, new Set([spec.key, 'disposition']), where),
    ];
  }
  if (entry.disposition === 'rendered') {
    return namedDispositionErrors(entry, where, spec.key, 'surface', 'rendered disposition');
  }
  if (entry.disposition === 'used') {
    return namedDispositionErrors(entry, where, spec.key, 'consumer', 'used disposition');
  }
  if (entry.disposition === 'deferred') {
    return deferredDispositionErrors(entry, where, spec.key, openIssues);
  }
  if (entry.disposition === 'not_applicable') {
    return namedDispositionErrors(entry, where, spec.key, 'reason', 'not_applicable disposition');
  }
  return [`${where}: unsupported disposition validator for "${entry.disposition}"`];
}

function checkDispositionEntry(entry, index, label, spec, seen, openIssues) {
  const where = `${label} ${spec.field} entry ${index}`;
  const errors = objectErrors(entry, where);
  if (errors.length > 0) return errors;
  errors.push(...dispositionKeyErrors(entry, where, spec, seen));
  errors.push(...dispositionShapeErrors(entry, where, spec, openIssues));
  return errors;
}

function dispositionSpecs(coverage) {
  return [
    {
      field: 'events',
      key: 'kind',
      label: 'EventType',
      values: coverage.events,
      statuses: new Set(['rendered', 'deferred']),
    },
    {
      field: 'queries',
      key: 'kind',
      label: 'query discriminant',
      values: coverage.queries,
      statuses: new Set(['used', 'deferred', 'not_applicable']),
    },
    {
      field: 'responses',
      key: 'family',
      label: 'response family',
      values: coverage.responses,
      statuses: new Set(['rendered', 'deferred', 'not_applicable']),
    },
    {
      field: 'extensions',
      key: 'contract',
      label: 'contract extension',
      values: CONTRACT_EXTENSIONS,
      statuses: new Set(['deferred']),
    },
  ];
}

function checkDispositionSection(data, label, spec, openIssues) {
  const entries = data[spec.field];
  if (!Array.isArray(entries)) return [`${label}: "${spec.field}" must be an array`];
  const errors = [];
  const seen = new Set();
  for (const [index, entry] of entries.entries()) {
    errors.push(...checkDispositionEntry(entry, index, label, spec, seen, openIssues));
  }
  for (const value of [...spec.values].sort()) {
    if (!seen.has(value)) errors.push(`${label}: ${spec.label} "${value}" has no disposition`);
  }
  return errors;
}

function checkDispositionTable(frontend, table, coverage, openIssues) {
  const label = `${DISPOSITIONS_RELATIVE}/${frontend}.json`;
  if (table.parseError) return [`${label}: invalid JSON: ${table.parseError}`];
  const errors = objectErrors(table.data, label);
  if (errors.length > 0) return errors;
  errors.push(...unknownFieldErrors(table.data, DISPOSITION_TABLE_FIELDS, label));
  if (table.data.frontend !== frontend) {
    errors.push(`${label}: "frontend" must equal the filename stem "${frontend}"`);
  }
  for (const spec of dispositionSpecs(coverage)) {
    errors.push(...checkDispositionSection(table.data, label, spec, openIssues));
  }
  return errors;
}

function checkIssueOwner(issue, index, label, seen) {
  const where = `${label} issue entry ${index}`;
  const errors = objectErrors(issue, where);
  if (errors.length > 0) return {errors, openIssue: null};
  errors.push(...unknownFieldErrors(issue, ISSUE_OWNER_FIELDS, where));
  let openIssue = null;
  if (!Number.isInteger(issue.number) || issue.number <= 0) {
    errors.push(`${where}: "number" must be a positive integer`);
  } else if (seen.has(issue.number)) {
    errors.push(`${where}: issue #${issue.number} is listed more than once`);
  } else {
    seen.add(issue.number);
    if (issue.state === 'open') openIssue = issue.number;
  }
  if (issue.state !== 'open') errors.push(`${where}: "state" must equal "open"`);
  for (const field of ['title', 'scope']) {
    if (typeof issue[field] !== 'string' || issue[field].trim() === '') {
      errors.push(`${where}: "${field}" must be a non-empty string`);
    }
  }
  return {errors, openIssue};
}

function issueOwnerErrors(data, label) {
  const errors = objectErrors(data, label);
  const openIssues = new Set();
  if (errors.length > 0) return {errors, openIssues};
  errors.push(...unknownFieldErrors(data, ISSUE_OWNERS_FIELDS, label));
  if (typeof data.verified_at !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(data.verified_at)) {
    errors.push(`${label}: "verified_at" must be an ISO calendar date`);
  }
  if (!Array.isArray(data.issues)) {
    errors.push(`${label}: "issues" must be an array`);
    return {errors, openIssues};
  }
  const seen = new Set();
  for (const [index, issue] of data.issues.entries()) {
    const result = checkIssueOwner(issue, index, label, seen);
    errors.push(...result.errors);
    if (result.openIssue !== null) openIssues.add(result.openIssue);
  }
  return {errors, openIssues};
}

async function readIssueOwners(corpus) {
  const label = ISSUE_OWNERS_RELATIVE;
  let data;
  try {
    data = JSON.parse(await readFile(join(corpus, ISSUE_OWNERS_RELATIVE), 'utf8'));
  } catch (error) {
    return {errors: [`${label}: cannot read valid JSON: ${error.message}`], openIssues: new Set()};
  }
  return issueOwnerErrors(data, label);
}

async function dispositionErrors(corpus, coverage, openIssues) {
  const dispositions = await readJsonDir(join(corpus, DISPOSITIONS_RELATIVE));
  if (dispositions.error) return [dispositions.error];

  const errors = [];
  const tables = new Map(dispositions.files.map(table => [table.stem, table]));
  for (const frontend of FRONTENDS) {
    const table = tables.get(frontend);
    if (table === undefined) {
      errors.push(`${DISPOSITIONS_RELATIVE}/${frontend}.json: required table is missing`);
    } else {
      errors.push(...checkDispositionTable(frontend, table, coverage, openIssues));
    }
  }
  for (const table of dispositions.files) {
    if (!FRONTENDS.includes(table.stem)) {
      errors.push(`${DISPOSITIONS_RELATIVE}/${table.name}: unknown frontend table "${table.stem}"`);
    }
  }
  return errors;
}

function checkStep(step, index, label, frames) {
  const errors = [];
  const where = `${label} step ${index}`;
  if (!STEP_DIRECTIONS.has(step?.dir)) {
    errors.push(`${where}: "dir" must be one of c2s, s2c`);
  }
  const hasFrame = step?.frame !== undefined;
  const hasExpect = step?.expect !== undefined;
  if (hasFrame === hasExpect) {
    errors.push(`${where}: exactly one of "frame" or "expect" is required`);
    return errors;
  }
  const message = hasFrame ? step.frame : step.expect;
  if (typeof message?.type !== 'string') {
    errors.push(`${where}: message needs a string "type"`);
  } else if (!frames.has(message.type)) {
    errors.push(`${where}: unknown protocol message type "${message.type}"`);
  }
  return errors;
}

function validRequiredSetup(setup) {
  return (
    Array.isArray(setup) &&
    setup.length > 0 &&
    new Set(setup).size === setup.length &&
    setup.every(capability => typeof capability === 'string' && SETUP_CAPABILITY.test(capability))
  );
}

function scenarioShapeErrors(data, stem, label, decisions) {
  const errors = [];
  if (data?.id !== stem) {
    errors.push(`${label}: "id" must equal the filename stem "${stem}"`);
  }
  if (!SCENARIO_ROLES.has(data?.role)) {
    errors.push(`${label}: "role" must be one of control, subscribe, chat`);
  }
  if (!Array.isArray(data?.transports) || data.transports.length === 0) {
    errors.push(`${label}: "transports" must be a non-empty array`);
  }
  for (const transport of data?.transports ?? []) {
    if (!KNOWN_TRANSPORTS.has(transport)) errors.push(`${label}: unknown transport "${transport}"`);
  }
  const used = Array.isArray(data?.decisions) ? data.decisions : [];
  if (used.length === 0) errors.push(`${label}: "decisions" must tag at least one WP-* token`);
  for (const token of used) {
    if (!decisions.has(token)) {
      errors.push(`${label}: decision "${token}" is not in the wire contract`);
    }
  }
  if (data?.required_setup !== undefined && !validRequiredSetup(data.required_setup)) {
    errors.push(`${label}: "required_setup" must contain unique kebab-case capability names`);
  }
  return {errors, used};
}

function scenarioClientErrors(data, label) {
  if (data?.clients === undefined) return [];
  if (typeof data.clients !== 'object' || data.clients === null || Array.isArray(data.clients)) {
    return [`${label}: "clients" must be an object`];
  }
  const errors = [];
  const entries = Object.entries(data.clients);
  if (entries.length < 2) errors.push(`${label}: "clients" must name at least two clients`);
  for (const [name, client] of entries) {
    if (!name) errors.push(`${label}: client names must be non-empty`);
    if (!KNOWN_TRANSPORTS.has(client?.transport)) {
      errors.push(`${label}: client "${name}" has unknown transport "${client?.transport}"`);
    } else if (!Array.isArray(data.transports) || !data.transports.includes(client.transport)) {
      errors.push(`${label}: client "${name}" uses a transport not listed by the scenario`);
    }
  }
  return errors;
}

function scenarioStepErrors(data, label, frames) {
  if (!Array.isArray(data?.steps) || data.steps.length === 0) {
    return [`${label}: "steps" must be a non-empty array`];
  }
  const errors = [];
  const clients = data?.clients;
  const hasClientMap = typeof clients === 'object' && clients !== null && !Array.isArray(clients);
  for (const [index, step] of data.steps.entries()) {
    errors.push(...checkStep(step, index, label, frames));
    const where = `${label} step ${index}`;
    if (clients === undefined && step?.client !== undefined) {
      errors.push(`${where}: "client" requires a scenario "clients" map`);
    } else if (hasClientMap && !Object.hasOwn(clients, step?.client)) {
      errors.push(`${where}: "client" must name a client declared by the scenario`);
    }
  }
  return errors;
}

function helperStepErrors(step, index, label, helper) {
  const where = `${label} step ${index}`;
  const errors = [];
  const sent = step?.dir === 'c2s';
  if (!STEP_DIRECTIONS.has(step?.dir)) errors.push(`${where}: "dir" must be one of c2s, s2c`);
  const message = sent ? step?.frame : step?.expect;
  if ((step?.frame === undefined) === (step?.expect === undefined) || message === undefined) {
    errors.push(`${where}: a c2s step needs "frame" and an s2c step needs "expect"`);
    return errors;
  }
  const known = sent ? helper.requests : helper.messages;
  if (typeof message?.type !== 'string') {
    errors.push(`${where}: message needs a string "type"`);
  } else if (!known.has(message.type)) {
    errors.push(
      `${where}: unknown host helper ${sent ? 'request' : 'message'} type "${message.type}"`,
    );
  }
  return errors;
}

function helperShapeErrors(data, label) {
  const errors = [];
  if (!HELPER_ROLES.has(data?.role)) errors.push(`${label}: "role" must be one of control, run`);
  const transports = Array.isArray(data?.transports) ? data.transports : [];
  if (transports.length === 0 || !transports.every(transport => HELPER_TRANSPORTS.has(transport))) {
    errors.push(`${label}: "transports" must be ["stdio"]`);
  }
  for (const field of ['decisions', 'clients']) {
    if (data?.[field] !== undefined)
      errors.push(`${label}: a host helper scenario has no "${field}"`);
  }
  return errors;
}

function checkHelperScenario(data, label, helper) {
  const errors = helperShapeErrors(data, label);
  const sent = [];
  if (!Array.isArray(data?.steps) || data.steps.length === 0) {
    errors.push(`${label}: "steps" must be a non-empty array`);
  } else {
    for (const [index, step] of data.steps.entries()) {
      errors.push(...helperStepErrors(step, index, label, helper));
      if (step?.dir === 'c2s' && typeof step?.frame?.type === 'string') sent.push(step.frame.type);
    }
  }
  return {errors, used: [], sent};
}

function checkHelperCoverage(helper, sent) {
  return [...helper.requests]
    .sort()
    .filter(type => !sent.has(type))
    .map(type => `host-helper.schema.json: request "${type}" is sent by no host helper scenario`);
}

function checkScenario(scenario, decisions, frames, helper) {
  const label = `scenarios/${scenario.name}`;
  if (scenario.parseError) {
    return {errors: [`${label}: invalid JSON: ${scenario.parseError}`], used: [], sent: []};
  }
  if (scenario.data?.protocol === HELPER_PROTOCOL) {
    const result = checkHelperScenario(scenario.data, label, helper);
    if (scenario.data.id !== scenario.stem) {
      result.errors.push(`${label}: "id" must equal the filename stem "${scenario.stem}"`);
    }
    return result;
  }
  if (scenario.data?.protocol !== undefined) {
    return {
      errors: [`${label}: unknown "protocol" "${scenario.data.protocol}"`],
      used: [],
      sent: [],
    };
  }
  const {errors, used} = scenarioShapeErrors(scenario.data, scenario.stem, label, decisions);
  errors.push(...scenarioClientErrors(scenario.data, label));
  errors.push(...scenarioStepErrors(scenario.data, label, frames));
  return {errors, used, sent: []};
}

function runnerMetadataErrors(data, stem, label) {
  const errors = [];
  if (data?.id !== stem) {
    errors.push(`${label}: "id" must equal the filename stem "${stem}"`);
  }
  if (typeof data?.description !== 'string' || data.description.trim() === '') {
    errors.push(`${label}: "description" must be a non-empty string`);
  }
  for (const field of Object.keys(data ?? {})) {
    if (!RUNNER_FIELDS.has(field)) errors.push(`${label}: unknown runner field "${field}"`);
  }
  return errors;
}

function validRunnerGroups(groups) {
  return (
    typeof groups === 'object' &&
    groups !== null &&
    !Array.isArray(groups) &&
    Object.keys(groups).length > 0
  );
}

function checkRunnerGroup(group, scenarios, label, registered, scenarioIds) {
  const errors = [];
  const executed = [];
  if (!SETUP_CAPABILITY.test(group)) {
    errors.push(`${label}: group name "${group}" must be kebab-case`);
  }
  if (!Array.isArray(scenarios) || scenarios.length === 0) {
    errors.push(`${label}: group "${group}" must be a non-empty scenario array`);
    return {errors, executed};
  }
  for (const scenario of scenarios) {
    if (typeof scenario !== 'string' || scenario.length === 0) {
      errors.push(`${label}: group "${group}" must contain scenario ids`);
      continue;
    }
    if (registered.has(scenario)) {
      errors.push(`${label}: scenario "${scenario}" is registered in more than one group`);
      continue;
    }
    registered.add(scenario);
    executed.push(scenario);
    if (!scenarioIds.has(scenario)) {
      errors.push(`${label}: group "${group}" names unknown scenario "${scenario}"`);
    }
  }
  return {errors, executed};
}

function checkRunner(runner, scenarioIds) {
  const label = `runners/${runner.name}`;
  if (runner.parseError) {
    return {errors: [`${label}: invalid JSON: ${runner.parseError}`], executed: []};
  }
  const errors = runnerMetadataErrors(runner.data, runner.stem, label);
  const executed = [];
  const groups = runner.data?.groups;
  if (!validRunnerGroups(groups)) {
    errors.push(`${label}: "groups" must be a non-empty object`);
    return {errors, executed};
  }
  const registered = new Set();
  for (const [group, scenarios] of Object.entries(groups)) {
    const result = checkRunnerGroup(group, scenarios, label, registered, scenarioIds);
    errors.push(...result.errors);
    executed.push(...result.executed);
  }
  return {errors, executed};
}

function checkExecutionPartition(scenarios, executed) {
  const errors = [];
  for (const scenario of scenarios) {
    if (scenario.parseError) continue;
    const label = `scenarios/${scenario.name}`;
    const isExecuted = executed.has(scenario.stem);
    const declaresSetup = scenario.data?.required_setup !== undefined;
    if (isExecuted && declaresSetup) {
      errors.push(`${label}: runner-executed scenario must not declare "required_setup"`);
    } else if (!isExecuted && !declaresSetup) {
      errors.push(`${label}: scenario must be runner-executed or declare "required_setup"`);
    }
  }
  return errors;
}

function checkDecisionCoverage(decisions, referenced) {
  const errors = [];
  for (const token of [...decisions].sort()) {
    if (!referenced.has(token)) {
      errors.push(`wire-protocol.md: decision "${token}" is exercised by no scenario`);
    }
  }
  return errors;
}

async function readProtocolSources(root) {
  try {
    const schema = JSON.parse(await readFile(join(root, 'clients', SCHEMA_RELATIVE), 'utf8'));
    const helper = helperTypes(
      JSON.parse(await readFile(join(root, 'clients', HELPER_SCHEMA_RELATIVE), 'utf8')),
    );
    const decisions = contractDecisions(await readFile(join(root, CONTRACT_RELATIVE), 'utf8'));
    return {schema, helper, decisions, error: null};
  } catch (error) {
    return {schema: null, helper: null, decisions: null, error: error.message};
  }
}

/** Collect every corpus violation. Pure over the repo root so tests can point it at a fixture tree. */
export async function corpusErrors(root) {
  const errors = [];
  const sources = await readProtocolSources(root);
  if (sources.error !== null) return [`cannot read protocol sources: ${sources.error}`];
  const {schema, helper, decisions} = sources;
  const kinds = eventKinds(schema);
  const queries = queryKinds(schema);
  const responses = responseFamilies(schema);
  const payloadKinds = eventDataKinds(schema);
  const allowedFields = runEventFields(schema);
  const requiredFields = runEventRequiredFields(schema);
  const frames = frameTypes(schema);

  const corpus = join(root, CORPUS_RELATIVE);
  const events = await readJsonDir(join(corpus, 'events'));
  if (events.error) errors.push(events.error);
  for (const fixture of events.files) {
    errors.push(...checkEventFixture(fixture, kinds, allowedFields, requiredFields));
  }
  errors.push(...checkEventCoverage(events.files, kinds));

  const payloads = await readJsonlDir(join(corpus, PAYLOADS_RELATIVE));
  if (payloads.error) errors.push(payloads.error);
  for (const fixture of payloads.files) {
    errors.push(...checkPayloadFixture(fixture, kinds, allowedFields, requiredFields));
  }
  errors.push(...checkPayloadCoverage(payloads.files, payloadKinds));

  const owners = await readIssueOwners(corpus);
  errors.push(...owners.errors);
  errors.push(
    ...(await dispositionErrors(corpus, {events: kinds, queries, responses}, owners.openIssues)),
  );

  const scenarios = await readJsonDir(join(corpus, 'scenarios'));
  if (scenarios.error) errors.push(scenarios.error);
  const scenarioIds = new Set(
    scenarios.files.filter(scenario => !scenario.parseError).map(scenario => scenario.stem),
  );
  const runners = await readJsonDir(join(corpus, RUNNERS_RELATIVE));
  if (runners.error) errors.push(runners.error);
  const executed = new Set();
  for (const runner of runners.files) {
    const result = checkRunner(runner, scenarioIds);
    errors.push(...result.errors);
    for (const scenario of result.executed) executed.add(scenario);
  }
  const referenced = new Set();
  const sent = new Set();
  for (const scenario of scenarios.files) {
    const result = checkScenario(scenario, decisions, frames, helper);
    errors.push(...result.errors);
    for (const token of result.used) referenced.add(token);
    for (const type of result.sent) sent.add(type);
  }
  errors.push(...checkHelperCoverage(helper, sent));
  errors.push(...checkExecutionPartition(scenarios.files, executed));
  errors.push(...checkDecisionCoverage(decisions, referenced));
  return errors;
}

async function main() {
  const root = resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', '..');
  const errors = await corpusErrors(root);
  if (errors.length === 0) {
    console.log('Conformance corpus is complete and well formed.');
    return 0;
  }
  console.error('Conformance corpus violations:');
  for (const error of errors) console.error(`- ${error}`);
  return 1;
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = await main();
}
