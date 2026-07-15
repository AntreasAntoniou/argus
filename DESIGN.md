# Argus — Design Record

> Argus Panoptes: the hundred-eyed watchman who never fully sleeps.
> A command center for live coding-agent fleets: every Claude Code session on every
> machine, one board, one question answered at a glance — **"which agent needs me?"**

Status: locked 2026-07-15 with Antreas via multiple-choice design review.
This file is the canonical spec. Deviations require an explicit note here.

## The problem

Agents work in bursts — 30–40s of thinking, a flurry of writes, then waiting.
Beyond 3–4 parallel agents, raw tmux panes actively work against you: the human
becomes the dashboard. Existing tools each nail one niche (Agent View: official CLI
dashboard; cmux/Conductor: local desktop orchestrators; Happy/Omnara: mobile remote;
tmux/claude-squad: persistence) but nobody ships **explicit state + interrupt routing
+ fleet federation** over sessions launched *any* way.

## Non-negotiable principles

1. **No gravity.** Argus observes sessions however they were born (anastasis fleet,
   bare `claude`, SSH boxes). Never a mandatory `argus run` wrapper.
2. **State, not text.** Every agent gets an explicit state machine
   (`starting / thinking / editing / testing / running / blocked / idle / done / dead`), never inferred
   by a human from scrollback. Dead-agent detection is first-class.
3. **Attention routing is the core primitive.** Blocked agents float to the top with
   their exact question; everything not needing the human dims.
4. **The terminal is the drill-down, the board is the default.**
5. **No secrets in the repo.** All config/tokens live in `~/.argus/` (gitignored
   pattern), repo is public from day one.

## Locked decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | State source | **Hybrid**: Claude Code hooks (real-time POST) + JSONL transcript watcher (backfill/catch-all) + tmux poll (liveness) |
| 2 | UI surface v1 | **TUI board** (runs in a tmux window, keyboard-driven); web board is v2 |
| 3 | Stack | **Python 3.14** + FastAPI + uvicorn; SSE for state stream; `watchfiles` for JSONL tailing; Textual for the TUI; dedicated conda env `argus`, uv-managed deps |
| 4 | v1 scope | **Full five layers**: state board, interrupt queue, semantic timeline, diff review, push-on-block + multi-machine federation |
| 5 | Reply path | **Guarded injection**: inline reply from board → `tmux capture-pane` verifies the queued prompt is still live → `send-keys`; stale ⇒ refresh card, never blind-inject. Enter always attaches. |
| 6 | Federation | **Full mesh**: every machine (mac, astrape, forge, odysseus) runs an identical `argusd`; static peer list in config; peers exchange full local state on an interval + push events immediately; any node can render the whole fleet. At N=4 this is symmetric state exchange, not a gossip protocol — keep it boring. |
| 7 | Push channel | **WhatsApp via agent-comms backend** (self-notify only), batched ≤1 msg / 5 min unless critical; digest format: count + per-agent question |
| 8 | Repo | **Public**, `AntreasAntoniou/argus`, MIT |
| 9 | Diff layer | Read-only `git status`/`diff` of each session's actual cwd + branch. No imposed worktree convention (follows from principle 1). |

## Architecture

```
┌─ per machine ──────────────────────────────────────────────┐
│  Claude Code hooks ──POST /hook──▶                          │
│  ~/.claude/projects/**/*.jsonl ──watchfiles──▶   argusd     │
│  tmux list-panes / capture-pane ──poll──▶      (FastAPI)    │
│                                                  │          │
│  git status/diff of session cwds ──on demand──▶  │          │
└──────────────────────────────────────────────────┼──────────┘
                                                   │ mesh sync (peer list in config)
                     mac ⇄ astrape ⇄ forge ⇄ odysseus
                                                   │
                    ┌──────────────────────────────┼─────────┐
                    │  argus TUI (any node, SSE)   │         │
                    │  WhatsApp push (batched)  ◀──┘         │
                    └────────────────────────────────────────┘
```

### Components

- **`argusd`** — the daemon. Ingesters (hook receiver, JSONL watcher, tmux poller),
  state reducer (events → per-session state machine), mesh sync, SSE API, push notifier.
- **`argus`** (TUI) — Textual app: departures-board layout
  (NEEDS YOU ↑ / working / quiet ↓), j/k navigation, y/n/typed inline replies,
  Enter = attach (`tmux switch-client` locally, ssh+attach hint for remote nodes),
  per-agent timeline drill-down, diff pane.
- **hook pack** — installable hook set (`argus install-hooks`) writing to
  `~/.claude/settings.json`: async POSTs for SessionStart, UserPromptSubmit,
  PreToolUse, PostToolUse, Notification, Stop, SubagentStop, SessionEnd.
- **state store** — in-memory + SQLite journal (survive daemon restart, feed timelines).

### State machine (per session)

`starting → thinking ⇄ tool:<name> (editing/testing/running) → blocked(question) | idle → … → done | dead`

- `blocked`: Notification hook (permission prompt / question) or idle-with-prompt
  detected via capture-pane.
- `idle`: session alive, prompt empty, not blocked — awaiting input with nothing
  queued (idle-with-prompt is `blocked`, per above).
- `dead`: tmux pane gone or JSONL silent past threshold while state ≠ done.
- Every transition carries: session_id, machine, cwd, branch, tokens, last tool,
  diff-stat cache.

### Semantic timeline

Collapsed event stream per agent derived from JSONL + hooks: tool calls, files
touched (+/- lines), tests run and results, tokens burned, questions asked.
Expandable rows; deep source of truth remains the transcript.

## v1 acceptance (the demo that must work)

From the Mac TUI: see live sessions on mac + astrape simultaneously; a permission
prompt on astrape floats to the top within 2s; answer `y` inline (guarded); watch
its state flip; kill a pane and see `dead` within 15s; walk away, get a batched
WhatsApp digest for a new block; drill into any agent's timeline and diff.

## Deferred (v2+)

Web board at antreas.io/dev/argus (same SSE API), macOS menubar satellite,
ntfy priority channel, phone inline replies, historical analytics.

## Amendments

- 2026-07-15: consistency fix — unified state vocabulary between principle 2 and
  the State machine section; no semantic design change.
