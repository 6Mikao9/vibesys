import {expect, test} from 'bun:test';
import {create, fromBinary, toBinary} from '@bufbuild/protobuf';
import type {HostKey} from '@vibesys/hosts';
import {FakeGatewayBridge} from '@vibesys/ipc/testing';
import {checkMachine} from '@vibesys/machine/testing';
import {
  type Record as ChatRecord,
  ChatRole,
  ChatSessionCreatedSchema,
  GetBlobsResponseSchema,
  HostCallRequestSchema,
  HostCallResponseSchema,
  ListChatSessionsResponseSchema,
  RecordSchema,
  TailRequestSchema,
  TailResponseSchema,
} from '@vibesys/proto';
import {CHAT_BLOBS_METHOD, CHAT_SESSIONS_METHOD} from '@vibesys/runs/chat';
import {SEEDS, Seeded} from '@vibesys/store/testing';
import {FakeClock} from '@vibesys/transport/testing';
import {waitFor} from 'xstate';
import {emptyHistory, foldChat, threadsMachine} from '../state/index.js';
import {chatService, threadsActor} from './index.js';

const metadata = (id: string) =>
  create(ChatSessionCreatedSchema, {id, provider: 'claude', model: 'opus'});
const record = (seq: number, requestId: string, role: ChatRole, text: string): ChatRecord =>
  create(RecordSchema, {
    seq: BigInt(seq),
    epoch: 1,
    at: {seconds: 1n},
    body: {
      case: 'chat',
      value: {
        event: {case: 'message', value: {requestId, role, content: {case: 'text', value: text}}},
      },
    },
  });

test('generated thread selections, creation, sends, reconnects and reordered replies retain identity and release ownership', async () => {
  for (const seed of SEEDS) {
    const random = new Seeded(seed);
    const bridge = new FakeGatewayBridge();
    const clock = new FakeClock();
    const ports = {
      bridge,
      host: 'local' as HostKey,
      runId: 'run-a',
      projectRoot: '/project',
      schedule: clock.schedule,
    };
    const service = chatService(ports);
    const actor = threadsActor(ports);
    const logs = new Map<string, ChatRecord[]>();
    const questions = new Map<string, string[]>();
    const pending: Array<{resolve: () => void; result: Promise<unknown>}> = [];
    let selected: string | null = null;
    let identity = 0;
    async function listing() {
      const call = bridge.calls.at(-1);
      expect(call?.method).toBe(CHAT_SESSIONS_METHOD);
      call?.resolve(
        toBinary(
          ListChatSessionsResponseSchema,
          create(ListChatSessionsResponseSchema, {sessions: [...logs.keys()].map(metadata)}),
        ),
      );
      await waitFor(actor, snapshot => snapshot.matches({listing: 'ready'}), {timeout: 1000});
    }
    function admit(result: Promise<unknown>) {
      const call = bridge.calls.at(-1);
      if (!call) throw new Error('Missing call');
      const method = fromBinary(HostCallRequestSchema, call.bytes).method;
      if (method.case === 'createChatSession') {
        const id = method.value.sessionId;
        if (!logs.has(id)) {
          logs.set(id, [
            create(RecordSchema, {
              seq: 1n,
              epoch: 1,
              at: {seconds: 1n},
              body: {case: 'chat', value: {event: {case: 'created', value: metadata(id)}}},
            }),
          ]);
          questions.set(id, []);
        }
        const bytes = toBinary(
          HostCallResponseSchema,
          create(HostCallResponseSchema, {
            result: {case: 'createChatSession', value: {sessionId: id}},
          }),
        );
        pending.push({result, resolve: () => call.resolve(bytes)});
      } else if (method.case === 'chatMessage') {
        const request = method.value;
        const log = logs.get(request.sessionId);
        if (!log) throw new Error('Unknown thread');
        log.push(record(log.length + 1, request.requestId, ChatRole.USER, request.text));
        questions.get(request.sessionId)?.push(request.requestId);
        const bytes = toBinary(
          HostCallResponseSchema,
          create(HostCallResponseSchema, {
            result: {
              case: 'chatMessage',
              value: {sessionId: request.sessionId, requestId: request.requestId},
            },
          }),
        );
        pending.push({result, resolve: () => call.resolve(bytes)});
      } else throw new Error('Unexpected operation');
    }
    async function publish() {
      if (selected === null) return;
      const stream = bridge.streams.filter(stream => !stream.cancelled).at(-1);
      if (!stream)
        throw new Error(
          `Missing session stream seed=${seed} selected=${selected} snapshot=${JSON.stringify(actor.getSnapshot().value)} streams=${bridge.streams.map(stream => `${fromBinary(TailRequestSchema, stream.bytes).sessionId}:${stream.cancelled}`).join(',')}`,
        );
      const request = fromBinary(TailRequestSchema, stream.bytes);
      expect(request.sessionId).toBe(selected);
      const log = logs.get(selected) ?? [];
      const records = log.filter(row => row.seq >= request.fromSeq);
      if (records.length > 0)
        stream.receive(
          toBinary(
            TailResponseSchema,
            create(TailResponseSchema, {
              records: records.map(row => toBinary(RecordSchema, row)),
              throughSeq: log.at(-1)?.seq ?? 0n,
            }),
          ),
        );
      await waitFor(
        actor,
        snapshot =>
          (snapshot.context.histories[selected as string]?.throughSeq ?? 0n) === BigInt(log.length),
        {timeout: 1000},
      );
      expect(
        actor.getSnapshot().context.histories[selected]?.turns.map(turn => turn.requestId) ?? [],
      ).toEqual(questions.get(selected) ?? []);
    }
    actor.start();
    try {
      await listing();
      for (let step = 0; step < 25; step++) {
        const action = random.int(6);
        const ids = [...logs.keys()];
        if (action === 0 || ids.length === 0) {
          const id = `thread-${identity++}`;
          admit(
            service.create(id, {provider: 'claude', model: 'opus'}, new AbortController().signal),
          );
        } else if (action === 1) {
          selected = random.bool() ? random.pick(ids) : null;
          actor.send({type: 'select', id: selected});
        } else if (action === 2) {
          const id = random.pick(ids);
          admit(
            service.send(
              id,
              `q-${identity++}`,
              `question-${seed}-${step}`,
              new AbortController().signal,
            ),
          );
        } else if (action === 3 && selected !== null) {
          bridge.streams
            .filter(stream => !stream.cancelled)
            .at(-1)
            ?.ended('lost');
          clock.advanceBy(1000);
        } else if (action === 4) {
          const id = random.pick(ids);
          const log = logs.get(id) as ChatRecord[];
          const unanswered = (questions.get(id) ?? []).filter(
            requestId =>
              !log.some(
                row =>
                  row.body.case === 'chat' &&
                  row.body.value.event.case === 'message' &&
                  row.body.value.event.value.requestId === requestId &&
                  row.body.value.event.value.role === ChatRole.ASSISTANT,
              ),
          );
          if (unanswered.length > 0)
            log.push(record(log.length + 1, random.pick(unanswered), ChatRole.ASSISTANT, 'answer'));
        } else {
          for (const call of pending.splice(0).reverse()) {
            call.resolve();
            await call.result;
          }
          actor.send({type: 'refresh'});
          await listing();
        }
        await publish();
      }
      for (const call of pending.splice(0).reverse()) {
        call.resolve();
        await call.result;
      }
    } finally {
      actor.stop();
    }
    expect(bridge.calls.every(call => call.cancelled)).toBe(true);
    expect(bridge.streams.every(stream => stream.cancelled)).toBe(true);
  }
});

test('generated reply completion orders preserve question order under replay', () => {
  for (const seed of SEEDS) {
    const random = new Seeded(seed);
    const count = 2 + random.int(20);
    const ids = Array.from({length: count}, (_, index) => `q-${index}`);
    let records = ids.map((id, index) => record(index + 1, id, ChatRole.USER, id));
    const remaining = [...ids];
    while (remaining.length > 0) {
      const [id] = remaining.splice(random.int(remaining.length), 1);
      records = [
        ...records,
        record(records.length + 1, id as string, ChatRole.ASSISTANT, `answer-${id}`),
      ];
    }
    const once = foldChat(emptyHistory, {records, blobs: new Map()});
    expect(once.turns.map(turn => turn.requestId)).toEqual(ids);
    expect(foldChat(once, {records, blobs: new Map()})).toEqual(once);
  }
});

test('thread discovery and selection own every generated operation and deadline', () => {
  checkMachine({
    machine: threadsMachine,
    input: undefined,
    event: (random, live) =>
      random.pick([
        {type: 'refresh'},
        {type: 'select', id: null},
        {type: 'select', id: 'thread-a'},
        ...live.operations
          .filter(id => id === 'list')
          .map(id => live.done(id, [metadata('thread-a')])),
        ...live.operations.map(id => live.error(id, new Error('lost'))),
        ...live.delays.map(id => live.fire(id)),
      ]),
    invariants: () => [],
  });
});

const blobRecord = (seq: number, requestId: string, sha256: string): ChatRecord =>
  create(RecordSchema, {
    seq: BigInt(seq),
    epoch: 1,
    at: {seconds: 1n},
    body: {
      case: 'chat',
      value: {
        event: {
          case: 'message',
          value: {
            requestId,
            role: ChatRole.USER,
            content: {case: 'blob', value: {sha256, size: 5n}},
          },
        },
      },
    },
  });

test('generated history failures keep confirmed messages and a retry replaces the failed stream', async () => {
  for (const seed of SEEDS) {
    const random = new Seeded(seed);
    const bridge = new FakeGatewayBridge();
    const clock = new FakeClock();
    const ports = {
      bridge,
      host: 'local' as HostKey,
      runId: 'run-a',
      projectRoot: '/project',
      schedule: clock.schedule,
    };
    const actor = threadsActor(ports);
    const log: ChatRecord[] = [
      create(RecordSchema, {
        seq: 1n,
        epoch: 1,
        at: {seconds: 1n},
        body: {case: 'chat', value: {event: {case: 'created', value: metadata('thread-a')}}},
      }),
    ];
    const live = () => bridge.streams.filter(stream => !stream.cancelled);
    const confirmed = () => actor.getSnapshot().context.histories['thread-a'] ?? emptyHistory;
    const deliver = (from: bigint) => {
      const records = log.filter(row => row.seq >= from);
      live()
        .at(-1)
        ?.receive(
          toBinary(
            TailResponseSchema,
            create(TailResponseSchema, {
              records: records.map(row => toBinary(RecordSchema, row)),
              throughSeq: log.at(-1)?.seq ?? 0n,
            }),
          ),
        );
    };
    async function settle(through: bigint) {
      await waitFor(
        actor,
        snapshot => (snapshot.context.histories['thread-a']?.throughSeq ?? 0n) >= through,
        {timeout: 1000},
      );
    }
    async function failBlob(call: (typeof bridge.calls)[number]) {
      call.resolve(
        toBinary(
          GetBlobsResponseSchema,
          create(GetBlobsResponseSchema, {
            blobs: [{sha256: 'h', errorCode: 5, errorMessage: 'lost'}],
          }),
        ),
      );
      await waitFor(actor, snapshot => snapshot.context.historyError !== null, {timeout: 1000});
    }
    actor.start();
    actor.send({type: 'select', id: 'thread-a'});
    try {
      for (let step = 0; step < 20; step++) {
        const before = confirmed();
        const action = random.int(4);
        const healthy = actor.getSnapshot().context.historyError === null;
        if (action === 0 && healthy) {
          log.push(record(log.length + 1, `q-${step}`, ChatRole.USER, `text-${step}`));
          deliver(before.throughSeq + 1n);
          await settle(BigInt(log.length));
        } else if (action === 1 && healthy) {
          log.push(blobRecord(log.length + 1, `b-${step}`, 'h'));
          deliver(before.throughSeq + 1n);
          const call = await waitForBlobCall(bridge);
          await failBlob(call);
          expect(confirmed().throughSeq).toBe(before.throughSeq);
          expect(confirmed().turns).toEqual(before.turns);
        } else if (action === 2 && actor.getSnapshot().context.historyError !== null) {
          const failed = live().at(-1);
          actor.send({type: 'reload'});
          expect(actor.getSnapshot().context.historyError).toBeNull();
          expect(failed?.cancelled).toBe(true);
          expect(live().length).toBe(1);
          const retried = fromBinary(
            TailRequestSchema,
            (live().at(-1) as {bytes: Uint8Array}).bytes,
          );
          expect(retried.fromSeq).toBe(before.throughSeq + 1n);
          expect(confirmed().turns).toEqual(before.turns);
          const blob = log.find(
            row =>
              row.body.case === 'chat' &&
              row.body.value.event.case === 'message' &&
              row.body.value.event.value.content.case === 'blob' &&
              row.seq > before.throughSeq,
          );
          deliver(retried.fromSeq);
          if (blob) {
            const call = await waitForBlobCall(bridge);
            call.resolve(
              toBinary(
                GetBlobsResponseSchema,
                create(GetBlobsResponseSchema, {
                  blobs: [{sha256: 'h', data: new TextEncoder().encode('hello')}],
                }),
              ),
            );
          }
          await settle(BigInt(log.length));
        } else {
          live().at(-1)?.ended('lost');
          clock.advanceBy(1000);
        }
        expect(confirmed().throughSeq >= before.throughSeq).toBe(true);
        expect(confirmed().turns.slice(0, before.turns.length)).toEqual(before.turns);
      }
    } finally {
      actor.stop();
    }
    expect(bridge.streams.every(stream => stream.cancelled)).toBe(true);
  }
});

async function waitForBlobCall(bridge: FakeGatewayBridge) {
  for (let attempt = 0; attempt < 50; attempt++) {
    const call = bridge.calls
      .filter(item => item.method === CHAT_BLOBS_METHOD && !item.cancelled)
      .at(-1);
    if (call) return call;
    await Promise.resolve();
  }
  throw new Error('Missing blob fetch');
}
