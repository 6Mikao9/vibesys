import {ChatPanel, type RunChatPorts, useRunChat} from '@vibesys/feature-run-chat/renderer';
import {runHarness} from '@vibesys/feature-run-chat/state';
import {
  type ChatServicePorts,
  chatService,
  useChatThreads,
} from '@vibesys/feature-run-sessions/renderer';
import {sessionCore} from '@vibesys/feature-run-sessions/state';
import {newRequestId} from '@vibesys/request-identity';
import type {CoreState} from '@vibesys/run-state';
import {SteerMode, SteerTargetKind} from '@vibesys/runs/chat';
import {addressedIntent, runReference} from '@vibesys/runs/intents';
import {Button, Stack, Text} from '@vibesys/ui';
import {useEffect, useMemo} from 'react';

/** Compose the existing chat UI with protobuf gateway operations and session records. */
export function useGatewayChat(
  input: ChatServicePorts & {core: CoreState | null; epoch: bigint; readOnly: boolean},
) {
  const ports = useMemo<ChatServicePorts>(
    () => ({
      bridge: input.bridge,
      host: input.host,
      runId: input.runId,
      projectRoot: input.projectRoot,
      schedule: input.schedule,
    }),
    [input.bridge, input.host, input.runId, input.projectRoot, input.schedule],
  );
  const service = useMemo(() => chatService(ports), [ports]);
  const threads = useChatThreads(ports, input.projectRoot !== '');
  const lifetime = useMemo(() => new AbortController(), [ports]);
  useEffect(() => () => lifetime.abort(), [lifetime]);
  const chatPorts: RunChatPorts = {
    harnesses: async () => ({
      kind: 'listed',
      harnesses: (await service.catalog(newRequestId(), lifetime.signal)).map(option => ({
        provider: option.provider,
        model: option.model,
        default: option.default,
        ...(option.display
          ? {
              display: {
                providerName: option.display.providerName,
                ...(option.display.modelName ? {modelName: option.display.modelName} : {}),
              },
            }
          : {}),
      })),
    }),
    createThread: async (harness, signal, requestId) => {
      try {
        const choice = harness ?? (input.core ? runHarness(input.core) : null);
        if (!choice) return {ok: false, message: 'Choose a harness and model'};
        const threadId = await service.create(`thread-${requestId}`, choice, signal);
        threads.refresh();
        return {ok: true, threadId};
      } catch (error) {
        return {ok: false, message: String(error)};
      }
    },
    ask: async (request, signal) => {
      try {
        await service.send(request.threadId, request.requestId, request.text, signal);
        return {ok: true};
      } catch (error) {
        return {ok: false, message: String(error)};
      }
    },
    control: async request => {
      try {
        const intent =
          request.action === 'withdraw'
            ? {case: 'withdraw' as const, value: {withdraws: request.withdraws}}
            : {
                case: 'steer' as const,
                value: {
                  text: request.text,
                  mode:
                    request.mode === 'standing'
                      ? SteerMode.STANDING
                      : request.mode === 'interrupt'
                        ? SteerMode.INTERRUPT
                        : SteerMode.NEXT_TURN,
                  target: {
                    kind:
                      request.target?.kind === 'role'
                        ? SteerTargetKind.ROLE
                        : request.target?.kind === 'attempt'
                          ? SteerTargetKind.ATTEMPT
                          : request.target?.kind === 'item'
                            ? SteerTargetKind.ITEM
                            : SteerTargetKind.RUN,
                    id: request.target?.id ?? '',
                  },
                },
              };
        await service.control(
          addressedIntent(
            runReference(input.projectRoot, input.runId),
            request.control_id,
            input.epoch,
            intent,
          ),
          lifetime.signal,
        );
        return {kind: 'acknowledged'};
      } catch (error) {
        return {kind: 'rejected', message: String(error)};
      }
    },
    newControlId: newRequestId,
  };
  const core = input.core ? sessionCore(input.core, threads.context) : null;
  const chat = useRunChat({
    identity: `${input.host}/${input.runId}`,
    core,
    ports: chatPorts,
    readOnly: input.readOnly,
    askable: true,
  });
  useEffect(() => {
    if (chat.open) threads.refresh();
  }, [chat.open, threads.refresh]);
  const shown = chat.open ? (chat.panel?.shown ?? null) : null;
  useEffect(() => threads.select(shown), [shown, threads.select]);
  const error = threads.context?.historyError ?? threads.context?.listError;
  return {
    ...chat,
    panel: chat.panel ? (
      <Stack gap="space-2" grow>
        {error ? (
          <Stack gap="space-1">
            <Text tone="err" title={error}>
              Unavailable
            </Text>
            <Button text="Retry history" press={threads.reload} />
          </Stack>
        ) : null}
        <ChatPanel {...chat.panel} />
      </Stack>
    ) : null,
  };
}
