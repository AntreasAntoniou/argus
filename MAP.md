# MAP.md — Argus Codebase Map

> Generated 2026-07-15 by the cartography pass. Argus is a **fleet command center for Claude Code sessions** — "the hundred-eyed watchman for your coding-agent fleet". The repo is currently at the **design stage**: it contains the locked design record, README, license, and ignore rules. No Python source exists yet; this map records the planned architecture from `DESIGN.md` so all workers share the same picture.

## 1. Overview / Architecture

Argus watches every Claude Code session across a static mesh of machines (`mac`, `astrape`, `forge`, `odysseus`) and surfaces the ones that **need you** — turning session text streams into a state machine and an interrupt queue.

```
Claude Code sessions (any machine)
  │
  ├─ hooks (async POST /hook) ──────────────┐   SessionStart, UserPromptSubmit,
  ├─ JSONL transcripts (watchfiles) ────────┤   Pre/PostToolUse, Notification,
  ├─ tmux poller (list-panes/capture-pane) ─┤   Stop, SubagentStop, SessionEnd
  │                                         ▼
  │                                    ┌─────────┐    full-mesh sync    ┌──────────┐
  │                                    │ argusd  │◄────────────────────►│ peer     │
  │                                    │ FastAPI │  (static peer list)  │ argusd's │
  │                                    └────┬────┘                      └──────────┘
  │              state reducer + SQLite journal
  │                       │
  │            ┌──────────┼──────────────┐
  │            ▼          ▼              ▼
  │       SSE API    WhatsApp push   timelines/diff-stat cache
  │            │    (batched digest,
  │            ▼     agent-comms)
  │      ┌───────────┐
  └──────┤ argus TUI │  Textual departures-board: NEEDS YOU / working / quiet
         └───────────┘  j/k nav · y/n/typed inline replies (guarded tmux
                        send-keys) · Enter=attach · timeline + diff drill-down
```

### Planned components (per DESIGN.md, locked 2026-07-15)

| Component | What it is |
|---|---|
| **argusd** | FastAPI daemon: three ingesters (hook receiver `POST /hook`, JSONL watcher via `watchfiles`, tmux poller), state reducer, full-mesh peer sync, SSE API, WhatsApp push notifier (DESIGN.md:67) |
| **argus (TUI)** | Textual app: departures-board layout (NEEDS YOU / working / quiet), j/k navigation, y/n/typed inline replies via guarded `tmux send-keys`, Enter attaches to the session, timeline + diff drill-down (DESIGN.md:69) |
| **hook pack** | `argus install-hooks` — writes async-POST hooks into `~/.claude/settings.json` for all 8 hook events (DESIGN.md:73) |
| **state store** | In-memory + SQLite journal — survives daemon restart, feeds timelines (DESIGN.md:76) |

### Per-session state machine (DESIGN.md:80)

```
starting → thinking ⇄ tool:<name> (editing/testing/running)
        → blocked(question) → … → done | dead
```

Transitions carry: `session_id`, machine, cwd, branch, tokens, last tool, diff-stat cache.

### Three ingestion paths (redundant by design)

1. **Hooks** — real-time async `POST /hook` from Claude Code hook events (lowest latency).
2. **JSONL transcript watcher** — `watchfiles` over `~/.claude/projects/**/*.jsonl` (catches sessions without hooks installed).
3. **tmux poller** — `tmux list-panes` / `capture-pane` (liveness + dead-session detection, and the channel for guarded `send-keys` replies).

### v1 acceptance bar (DESIGN.md:96)

mac + astrape live sessions; a blocked session floats to the top of the board in ≤2s; guarded inline `y` reply works; dead detection in ≤15s; batched WhatsApp digest; timeline + diff drill-down.

## 2. Entry points

No code exists yet. The design-locked entry points that v1 must provide:

| Entry point | Kind | Role |
|---|---|---|
| `argusd` | daemon | FastAPI service — ingestion, state reduction, mesh sync, SSE, notifications |
| `argus` | CLI/TUI | Textual departures-board fleet console |
| `argus install-hooks` | CLI subcommand | Installs the async-POST hook pack into `~/.claude/settings.json` |
| `POST /hook` | HTTP endpoint | Real-time Claude Code hook event ingestion into argusd |
| `DESIGN.md` | document | Canonical locked design record — the authoritative spec for all build work |

## 3. Per-directory file table

### `/` (repo root — the only directory)

| File | Language | Role | Key symbols / contents |
|---|---|---|---|
| `DESIGN.md` | Markdown | **Canonical design record (locked 2026-07-15).** Principles, 9 locked decisions, architecture, components, per-session state machine, v1 acceptance criteria, v2 deferrals. | Component specs: `argusd` (:67), `argus` TUI (:69), hook pack / `argus install-hooks` (:73), state store (:76); `POST /hook` endpoint (:50); session state machine (:80); v1 acceptance demo (:96) |
| `README.md` | Markdown | Public-facing overview: pitch, ASCII board mockup, why (state-not-text, interrupt queue, no gravity, fleet-wide mesh, drill-down), the three ingestion paths, status (early), MIT. Links to DESIGN.md. | — |
| `LICENSE` | Text | MIT License, Copyright (c) 2026 Antreas Antoniou. | — |
| `.gitignore` | gitignore | Python build/cache artifacts, venvs, coverage, SQLite DBs, `.env`, `config.local.toml` — enforces "no secrets in repo"; runtime state lives in `~/.argus/`. | — |

## 4. Dependency sketch

### Internal
```
README.md ──links──▶ DESIGN.md        (design record is the source of truth)
.gitignore ──enforces──▶ DESIGN.md's "runtime state in ~/.argus/, no secrets" principle
```

### External (planned runtime stack, from DESIGN.md)

| Dependency | Used by / for |
|---|---|
| Python 3.14 | Entire project |
| FastAPI + uvicorn | argusd daemon, `POST /hook`, SSE API |
| SSE | argusd → TUI live event stream |
| watchfiles | JSONL transcript watcher over `~/.claude/projects/**/*.jsonl` |
| Textual | `argus` TUI departures board |
| tmux (`list-panes` / `capture-pane` / `send-keys` / `switch-client`) | Session poller, guarded inline replies, attach |
| SQLite | State journal (restart survival, timelines) |
| `~/.claude/settings.json` | Hook pack installation target |
| `~/.claude/projects/**/*.jsonl` | Transcript ingestion source |
| git (`status` / `diff`) | Diff-stat cache, drill-down diffs |
| agent-comms WhatsApp backend | Batched push digests |
| `~/.argus/` config | Static peer list: `mac`, `astrape`, `forge`, `odysseus` |

## 5. Notes for workers

- **DESIGN.md is locked** — build to it; deviations need an explicit design change, not silent drift.
- No package scaffolding (`pyproject.toml`, `src/`, tests) exists yet — the first build wave creates it.
- `.gitignore` already anticipates the runtime shape: SQLite DBs, `.env`, and `config.local.toml` never enter the repo; all runtime state belongs in `~/.argus/`.
