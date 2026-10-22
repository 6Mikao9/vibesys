/** Author the existing UI scenarios as dense native session records at the wire boundary. */
import {create, type MessageInitShape, toBinary} from '@bufbuild/protobuf';
import type {Page} from '@playwright/test';
import type {RunEvent} from '@vibesys/contracts';
import {
  ChatRole,
  RecordSchema,
  SteerMode,
  SteerTargetKind,
  type Record as StoreRecord,
  TailResponseSchema,
} from '@vibesys/proto';

const histories = new WeakMap<Page, Map<string, StoreRecord[]>>();
export async function emitChat(page: Page, events: readonly RunEvent[]): Promise<void> {
  const sessions = histories.get(page) ?? new Map<string, StoreRecord[]>();
  histories.set(page, sessions);
  const updates = new Map<string, StoreRecord[]>();
  for (const event of events) {
    const id = event.chat_thread_id ?? 'default';
    const records = sessions.get(id) ?? [];
    sessions.set(id, records);
    const batch = updates.get(id) ?? [];
    updates.set(id, batch);
    const append = (value: NonNullable<MessageInitShape<typeof RecordSchema>['body']>) => {
      const record = create(RecordSchema, {
        seq: BigInt(records.length + 1),
        epoch: 1,
        at: {seconds: 1n},
        body: value,
      });
      records.push(record);
      batch.push(record);
    };
    const message = (requestId: string, role: ChatRole, text: string) =>
      append({
        case: 'chat',
        value: {
          event: {case: 'message', value: {requestId, role, content: {case: 'text', value: text}}},
        },
      });
    const requestId =
      event.invocation_id ??
      (event.data?.kind === 'chat' ? event.data.invocation_id : null) ??
      `q-${event.sequence}`;
    const userExists = () =>
      records.some(
        record =>
          record.body.case === 'chat' &&
          record.body.value.event.case === 'message' &&
          record.body.value.event.value.requestId === requestId &&
          record.body.value.event.value.role === ChatRole.USER,
      );
    const data = event.data;
    if (data?.kind === 'chat_thread_created')
      append({
        case: 'chat',
        value: {
          event: {
            case: 'created',
            value: {id, title: data.title ?? '', provider: data.provider, model: data.model},
          },
        },
      });
    else if (data?.kind === 'agent_execution_started') {
      if (!userExists()) message(requestId, ChatRole.USER, data.user_prompt ?? '');
    } else if (data?.kind === 'chat') {
      if (!userExists()) message(requestId, ChatRole.USER, event.text ?? '');
      message(requestId, ChatRole.ASSISTANT, data.answer);
    } else if (data?.kind === 'agent_output_chunk' && data.channel === 'assistant') {
      if (!userExists()) message(requestId, ChatRole.USER, '');
      append({
        case: 'chat',
        value: {event: {case: 'token', value: {requestId, text: data.content}}},
      });
    } else if (data?.kind === 'steer_proposed') {
      append({
        case: 'chat',
        value: {
          event: {
            case: 'proposedSteer',
            value: {
              id: data.proposal_id,
              requestId,
              text: data.proposal.text,
              mode:
                data.proposal.mode === 'standing'
                  ? SteerMode.STANDING
                  : data.proposal.mode === 'interrupt'
                    ? SteerMode.INTERRUPT
                    : SteerMode.NEXT_TURN,
              target: {
                kind:
                  data.proposal.target?.kind === 'role'
                    ? SteerTargetKind.ROLE
                    : data.proposal.target?.kind === 'attempt'
                      ? SteerTargetKind.ATTEMPT
                      : data.proposal.target?.kind === 'item'
                        ? SteerTargetKind.ITEM
                        : SteerTargetKind.RUN,
                id: data.proposal.target?.id ?? '',
              },
            },
          },
        },
      });
    }
  }
  for (const [id, batch] of updates)
    if (batch.length > 0) {
      const bytes = [
        ...toBinary(
          TailResponseSchema,
          create(TailResponseSchema, {
            records: batch.map(record => toBinary(RecordSchema, record)),
            throughSeq: batch.at(-1)?.seq ?? 0n,
          }),
        ),
      ];
      await page.evaluate(
        ({id, bytes}) =>
          (
            window as unknown as {fakeGateway: {seedChat(id: string, bytes: number[]): void}}
          ).fakeGateway.seedChat(id, bytes),
        {id, bytes},
      );
    }
}
