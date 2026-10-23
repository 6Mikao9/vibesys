/** Gateway screen control owns the loader, content request and selected log follower. */
import {emptyLogView, foldChunk, type LogView} from '@vibesys/logs';
import {unprovided} from '@vibesys/machine';
import type {
  LoadProgress,
  RunLivenessHint,
  SkeletonLoad,
  SkeletonLoadFailure,
} from '@vibesys/run-skeleton';
import {type RecordRow, recordRows} from '@vibesys/run-state/records';
import type {SkeletonContent} from '@vibesys/run-state/skeleton';
import {assign, fromCallback, setup} from 'xstate';
import {
  type RunPageAction,
  type RunPageSeed,
  type RunPageState,
  runPageInitial,
  runPageStep,
} from './run-page.js';

export interface GatewayInput {
  readonly seed: RunPageSeed;
}
export interface GatewayLogInput {
  readonly runId: string;
  readonly streamId: string;
  readonly receive: (event: GatewayEvent) => void;
}
export type GatewayEvent =
  | {type: 'refresh-writer'}
  | {type: 'ui'; action: RunPageAction}
  | {type: 'log'; streamId: string; text: string; offset: number; next: number}
  | {type: 'log-failed'}
  | {type: 'records'; rows: readonly RecordRow[]}
  | {type: 'retry'}
  | {
      type: 'loaded';
      load: SkeletonLoad | undefined;
      progress: LoadProgress;
      failure: SkeletonLoadFailure | undefined;
      terminal: boolean;
      hint?: RunLivenessHint;
    };
export interface GatewayRunContext extends GatewayInput {
  load: SkeletonLoad | undefined;
  progress: LoadProgress;
  failure: SkeletonLoadFailure | undefined;
  terminal: boolean;
  hint: RunLivenessHint | undefined;
  ui: RunPageState;
  text: Readonly<Record<string, string>>;
  contentHash: string;
  attemptedContent: ReadonlySet<string>;
  replies: NonNullable<SkeletonContent['replies']>;
  error: string | null;
}

function nextContentHash(context: GatewayRunContext): string {
  const epochs = context.load?.skeleton.epochs ?? [];
  for (let index = epochs.length - 1; index >= 0; index -= 1) {
    const hash = epochs[index]?.epoch?.run?.objective?.sha256 ?? '';
    if (hash !== '' && context.text[hash] === undefined && !context.attemptedContent.has(hash))
      return hash;
  }
  return (
    Object.values(context.replies)
      .flat()
      .map(ref => ref.hash)
      .find(hash => context.text[hash] === undefined && !context.attemptedContent.has(hash)) ?? ''
  );
}

function replyAttachments(
  previous: GatewayRunContext['replies'],
  rows: readonly RecordRow[],
): GatewayRunContext['replies'] {
  const replies = {...previous};
  for (const row of rows) {
    const attachment = row.attachment;
    if (attachment?.role !== 'reply' || !attachment.blob) continue;
    replies[attachment.span] = [
      ...(replies[attachment.span] ?? []),
      {seq: BigInt(row.seq), hash: attachment.blob.sha256},
    ];
  }
  return replies;
}

/** Presentation changes reuse the existing pure reducer; gateway commands never reach legacy ports. */
function uiStep(state: RunPageState, action: RunPageAction): RunPageState {
  switch (action.type) {
    case 'view':
    case 'ideas-filter':
    case 'usage-controls':
    case 'ideas-layout':
    case 'ideas-open':
    case 'ideas-pin':
    case 'ideas-hover':
    case 'agents-open':
    case 'select-stream':
    case 'view-logs':
      return runPageStep(state, action).state;
    default:
      return state;
  }
}

function receivedLog(
  context: GatewayRunContext,
  event: Extract<GatewayEvent, {type: 'log'}>,
): Readonly<Record<string, LogView>> {
  const previous =
    (Object.hasOwn(context.ui.views, event.streamId)
      ? context.ui.views[event.streamId]
      : undefined) ?? emptyLogView();
  return {
    ...context.ui.views,
    [event.streamId]: foldChunk(previous, {
      offset: event.offset,
      next_offset: event.next,
      data: event.text,
      truncated_before: false,
      eof: false,
      closed: false,
      pipes: [],
    }),
  };
}

export const gatewayRunMachine = setup({
  types: {context: {} as GatewayRunContext, input: {} as GatewayInput, events: {} as GatewayEvent},
  actors: {
    loader: fromCallback<GatewayEvent, {runId: string}>(() => {
      throw new Error('loader not provided');
    }),
    content: unprovided<{hash: string; text: string}, {runId: string; hash: string}>(
      'content by hash',
    ),
    log: unprovided<void, GatewayLogInput>('log transport'),
  },
  guards: {
    needsContent: ({context}) => nextContentHash(context) !== '',
  },
  actions: {
    receivedRecords: assign(({context, event}) => {
      if (event.type !== 'records') return {};
      const rows = recordRows(context.ui.recordLog ?? [], event.rows);
      return {
        replies: replyAttachments(context.replies, event.rows),
        ui: {...context.ui, recordLog: rows, recordLogError: null},
      };
    }),
  },
}).createMachine({
  id: 'gatewayRun',
  context: ({input}) => ({
    ...input,
    load: undefined,
    progress: {phase: 'skeleton'},
    failure: undefined,
    terminal: false,
    hint: undefined,
    ui: runPageInitial(input.seed),
    text: {},
    contentHash: '',
    attemptedContent: new Set(),
    replies: {},
    error: null,
  }),
  initial: 'active',
  states: {
    active: {
      entry: assign({
        load: undefined,
        progress: {phase: 'skeleton'},
        failure: undefined,
        terminal: false,
        hint: undefined,
        contentHash: '',
        attemptedContent: new Set(),
        replies: {},
        ui: ({context}) => {
          const {recordLog: _rows, recordLogError: _error, ...rest} = context.ui;
          return {...rest, selected: null, views: {}};
        },
      }),
      type: 'parallel',
      on: {
        loaded: {
          actions: assign({
            load: ({event}) => event.load,
            progress: ({event}) => event.progress,
            failure: ({event}) => event.failure,
            terminal: ({event}) => event.terminal,
            hint: ({event}) => event.hint,
          }),
        },
        records: {actions: 'receivedRecords'},
        retry: {target: '#gatewayRun.active', reenter: true},
        ui: {actions: assign({ui: ({context, event}) => uiStep(context.ui, event.action)})},
      },
      states: {
        writer: {
          initial: 'following',
          on: {'refresh-writer': {target: '.following', reenter: true}},
          states: {
            following: {
              invoke: {
                id: 'loader',
                src: 'loader',
                input: ({context}) => ({runId: context.seed.identity.runId}),
                onError: {actions: assign({error: 'Unavailable'})},
              },
            },
          },
        },
        content: {
          initial: 'idle',
          states: {
            idle: {
              always: {
                guard: 'needsContent',
                target: 'reading',
                actions: assign(({context}) => {
                  const hash = nextContentHash(context);
                  return {
                    contentHash: hash,
                    attemptedContent: new Set([...context.attemptedContent, hash]),
                  };
                }),
              },
            },
            reading: {
              invoke: {
                id: 'content',
                src: 'content',
                input: ({context}) => ({
                  runId: context.seed.identity.runId,
                  hash: context.contentHash,
                }),
                onDone: {
                  target: 'idle',
                  actions: assign({
                    text: ({context, event}) => ({
                      ...context.text,
                      [event.output.hash]: event.output.text,
                    }),
                  }),
                },
                onError: {target: 'idle', actions: assign({error: 'Content unavailable'})},
              },
              after: {30000: {target: 'idle', actions: assign({error: 'Content timeout'})}},
            },
          },
        },
        logs: {
          initial: 'idle',
          on: {
            ui: [
              {
                guard: ({event, context}) =>
                  event.action.type === 'select-stream' &&
                  (event.action.streamId !== context.ui.selected ||
                    context.ui.logProblem !== undefined),
                target: '.following',
                actions: assign({
                  ui: ({context, event}) => ({
                    ...uiStep(context.ui, event.action),
                    logProblem: undefined,
                  }),
                }),
              },
              {actions: assign({ui: ({context, event}) => uiStep(context.ui, event.action)})},
            ],
          },
          states: {
            idle: {},
            following: {
              invoke: {
                id: 'log',
                src: 'log',
                input: ({context, self}) => ({
                  runId: context.seed.identity.runId,
                  streamId: context.ui.selected ?? '',
                  receive: event => self.send(event),
                }),
                onError: {
                  target: 'idle',
                  actions: assign({
                    ui: ({context}) => ({
                      ...context.ui,
                      logProblem: {streamId: context.ui.selected ?? '', message: 'Log unavailable'},
                    }),
                  }),
                },
              },
              on: {
                log: {
                  actions: assign({
                    ui: ({context, event}) => ({
                      ...context.ui,
                      views: receivedLog(context, event),
                      logProblem: undefined,
                    }),
                  }),
                },
                'log-failed': {
                  target: 'idle',
                  actions: assign({
                    ui: ({context}) => ({
                      ...context.ui,
                      logProblem: {streamId: context.ui.selected ?? '', message: 'Log unavailable'},
                    }),
                  }),
                },
              },
            },
          },
        },
      },
    },
  },
});
