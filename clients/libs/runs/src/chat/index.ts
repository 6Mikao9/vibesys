/** Session transport uses the existing HostCall, Tail and GetBlobs paths. */
import {clone, create, fromBinary, toBinary} from '@bufbuild/protobuf';
import {getTextEncoding} from '@bufbuild/protobuf/wire';
import {
  type ChatMessageRequest,
  ChatMessageRequestSchema,
  type ChatMessageResponse,
  type ChatSessionCreated,
  type ChatSteerProposed,
  type CreateChatSessionRequest,
  GetBlobsRequestSchema,
  GetBlobsResponseSchema,
  type HarnessOption,
  HostCallRequestSchema,
  HostCallResponseSchema,
  HostService,
  ListChatSessionsRequestSchema,
  ListChatSessionsResponseSchema,
  ReadRequestSchema,
  ReadResponseSchema,
  type Record,
  RecordSchema,
  TailRequestSchema,
  TailResponseSchema,
  validatedExactFromBinary,
} from '@vibesys/proto';
import {addressedIntent, IntentRefused, type RunReference} from '../intents/index.js';

export type {
  ChatMessage,
  ChatMessageRequest,
  ChatMessageResponse,
  ChatSteerProposed,
  Record,
} from '@vibesys/proto';
export {ChatRole, SteerMode, SteerTargetKind} from '@vibesys/proto';
export {HOST_CALL_METHOD, INTENT_TAIL_METHOD as CHAT_TAIL_METHOD} from '../intents/index.js';
export const CHAT_BLOBS_METHOD = `/${HostService.typeName}/${HostService.method.getBlobs.name}`;

export function chatRequest(identity: Omit<ChatMessageRequest, '$typeName'>): ChatMessageRequest {
  return create(ChatMessageRequestSchema, identity);
}
export function hostChatRequest(request: ChatMessageRequest): Uint8Array {
  return toBinary(
    HostCallRequestSchema,
    create(HostCallRequestSchema, {method: {case: 'chatMessage', value: request}}),
  );
}
export function hostChatResponse(
  bytes: Uint8Array,
  request: ChatMessageRequest,
): ChatMessageResponse {
  const result = fromBinary(HostCallResponseSchema, bytes).result;
  if (result.case === 'configurationFailed') throw new IntentRefused(result.value);
  if (
    result.case !== 'chatMessage' ||
    result.value.requestId !== request.requestId ||
    result.value.sessionId !== request.sessionId
  )
    throw new Error('Chat admission identity mismatch');
  return result.value;
}
export function chatTailRequest(runId: string, sessionId: string, fromSeq: bigint): Uint8Array {
  return toBinary(TailRequestSchema, create(TailRequestSchema, {runId, sessionId, fromSeq}));
}
export function chatTailResponse(bytes: Uint8Array): readonly Record[] {
  return fromBinary(TailResponseSchema, bytes).records.map(record =>
    fromBinary(RecordSchema, record),
  );
}
export function chatBlobRequest(
  runId: string,
  sessionId: string,
  hashes: readonly string[],
): Uint8Array {
  return toBinary(
    GetBlobsRequestSchema,
    create(GetBlobsRequestSchema, {runId, sessionId, sha256: [...hashes]}),
  );
}
export function chatBlobResponse(bytes: Uint8Array): ReadonlyMap<string, string> {
  const result = new Map<string, string>();
  for (const blob of fromBinary(GetBlobsResponseSchema, bytes).blobs) {
    if (blob.errorCode !== 0) throw new Error(blob.errorMessage);
    result.set(blob.sha256, getTextEncoding().decodeUtf8(blob.data, true));
  }
  return result;
}
export function chatBlobHashes(records: readonly Record[]): readonly string[] {
  return [
    ...new Set(
      records.flatMap(record => {
        const event = record.body.case === 'chat' ? record.body.value.event : null;
        return event?.case === 'message' && event.value.content.case === 'blob'
          ? [event.value.content.value.sha256]
          : [];
      }),
    ),
  ];
}
export function proposalIntent(
  identity: RunReference & {requestId: string; expectedEpoch: bigint},
  proposal: ChatSteerProposed,
) {
  return addressedIntent(identity, identity.requestId, identity.expectedEpoch, {
    case: 'steer',
    value: {text: proposal.text, target: proposal.target, mode: proposal.mode},
  });
}

export function copyChatRequest(request: ChatMessageRequest): ChatMessageRequest {
  return clone(ChatMessageRequestSchema, request);
}

export type {ChatSessionCreated, CreateChatSessionRequest, HarnessOption} from '@vibesys/proto';
export const CHAT_SESSIONS_METHOD = `/${HostService.typeName}/${HostService.method.listChatSessions.name}`;
export const CHAT_CATALOG_METHOD = `/${HostService.typeName}/${HostService.method.read.name}`;

export function sessionListRequest(runId: string): Uint8Array {
  return toBinary(ListChatSessionsRequestSchema, create(ListChatSessionsRequestSchema, {runId}));
}
export function sessionListResponse(bytes: Uint8Array): readonly ChatSessionCreated[] {
  return validatedExactFromBinary(ListChatSessionsResponseSchema, bytes).sessions;
}
export function sessionCreateRequest(
  request: Omit<CreateChatSessionRequest, '$typeName'>,
): Uint8Array {
  return toBinary(
    HostCallRequestSchema,
    create(HostCallRequestSchema, {
      method: {case: 'createChatSession', value: request},
    }),
  );
}
export function sessionCreateResponse(bytes: Uint8Array, sessionId: string): string {
  const result = validatedExactFromBinary(HostCallResponseSchema, bytes).result;
  if (result.case === 'configurationFailed') throw new IntentRefused(result.value);
  if (result.case !== 'createChatSession' || result.value.sessionId !== sessionId)
    throw new Error('Chat creation identity mismatch');
  return sessionId;
}
export function chatCatalogRequest(requestId: string): Uint8Array {
  return toBinary(
    ReadRequestSchema,
    create(ReadRequestSchema, {
      method: {case: 'listHarnesses', value: {requestId, withGenerations: true}},
    }),
  );
}
export function chatCatalogResponse(
  bytes: Uint8Array,
  requestId: string,
): readonly HarnessOption[] {
  const result = validatedExactFromBinary(ReadResponseSchema, bytes).result;
  if (result.case === 'failure') throw new Error(result.value.message);
  if (result.case !== 'listHarnesses' || result.value.requestId !== requestId)
    throw new Error('Chat catalogue identity mismatch');
  return result.value.harnesses;
}
