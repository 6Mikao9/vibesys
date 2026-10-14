# Web run viewer parity

This checklist records the generic Web viewer's deliberate boundary with the TUI. Both clients fold
the same `@vibesys/run-state` projection. Web presentation state stays in `feature-run-view`.

This document owns view-level product scope. The schema-derived, per-event source of truth is
[`tests/conformance/rendering-dispositions/web.json`](https://github.com/uw-syfi/vibesys/blob/main/tests/conformance/rendering-dispositions/web.json),
enforced by the conformance gate together with the corresponding TUI table. When an event row and
this summary disagree, update this summary to match the event table. The browser and Electron use
the same Web disposition because the desktop shell does not own a second renderer.

| Surface | Web v1 | TUI counterpart or owner |
| --- | --- | --- |
| Run status, rounds, agent activity, usage | Implemented from `CoreState`; the keyboard-accessible round rail filters activity by semantic round identity | Run map and status panes |
| Transcript and typed tool calls | Implemented as a tail-first fixed-row virtual transcript; scrolling back preserves the historical position while new events arrive | Activity transcript |
| Pause, resume, steer, stop | Implemented for live sessions, disabled on replay | Command bar, #783 |
| Stream and control recovery | Implemented by `WebSession` and its independent banners | Session controller |
| Experiment chat | Implemented in the live conversation panel; replay remains read-only | Conversation pane |
| Transcript search and arbitrary filters | Deferred; semantic round drill-down is implemented | #799 |
| Evidence and diagnostic drill-downs | Deferred | #789 |
| Annotations | Deferred | #805 |
| Multi-run navigation | Deferred | #816 |
| Server-selected theme tokens | Implemented as complete contrast-checked palettes for every `tui-defaults` theme. A Web theme picker and local overrides remain deferred to #2168. | TUI theme picker, #2168 |

The viewer must not recreate session or reducer policy in React. A new shared semantic projection
belongs in `@vibesys/run-state`; a Web-only view state belongs in `@vibesys/feature-run-view`.

Campaign workspace frames are not part of `EventType` and therefore are not hand-written into these
event tables. #1761 owns their generated transport contract. When that generated kind set exists, it
must extend this gate with its own shared fixtures and exhaustive frontend dispositions rather than
introducing a browser-authored wire type.
