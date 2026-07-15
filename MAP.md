# Argus — Codebase Map

> **Argus** — the hundred-eyed watchman for a fleet of coding agents. A daemon
> ingests Claude Code activity (hooks + JSONL transcripts + tmux), reduces it
> into a per-session state machine, federates state across machines in a full
> mesh, and renders a "departures-board" TUI plus push notifications when an
> agent is **blocked and needs you**.

## Overview / Architecture

Argus is a two-process system driven by one shared contract:

- **`argusd`** — the daemon. A FastAPI app that receives Claude Code **hook**
  POSTs, tails **JSONL transcripts**, and reads **tmux** panes; folds every
  signal into `SessionSnapshot`s via a pure reducer; persists an append-only
  SQLite event journal; federates full state across peer machines; and pushes a
  batched WhatsApp digest when sessions go BLOCKED.
- **`argus`** — the TUI. A Textual departures-board that streams `FleetState`
  over SSE from the daemon and lets you reply to blocked agents (guarded tmux
  keystroke injection) or attach.

**Data flow:**

```
hooks POST ─┐
transcripts ─┼─► Event ──► reduce() ──► SessionSnapshot ──► SessionStore ──► FleetState
tmux panes ─┘                (pure)         (+SQLite journal)        │
                                                                     ├─► SSE /api/state ──► TUI board
                                                                     ├─► Federation (full-mesh httpx) ──► peers
                                                                     └─► NotifyBatcher ──► WhatsApp digest
```

**State machine** (`SessionStatus`): STARTING → THINKING → TOOL → BLOCKED /
IDLE / DONE / DEAD. `needs_you` == BLOCKED; `is_terminal` == DONE|DEAD.
Sessions bucket into **needs_you** (oldest-first), **working**, **quiet**.

**Contract discipline:** `models.py` and `config.py` are **IMPLEMENTED and
frozen** — the single source of truth every module codes against. Almost
everything else is a **typed stub** (`NotImplementedError` / raising bodies)
with intended-behavior tests marked `xfail(strict=False)`. See
`SHARED_INTERFACES.md` for the authoritative seam doc and `DESIGN.md` for the
decision record.

### Implementation status

| State | Modules |
|---|---|
| **Implemented (frozen contract)** | `models.py`, `config.py`, `__init__.py` |
| **Partial** | `cli.py` (HOOK_EVENTS/`hook_command`/`build_parser`/install-hooks done; serve + TUI bodies stub), `notify.py` (`Digest.render` + `NoopNotifier` done; WhatsApp + batcher stub) |
| **Stub** | `daemon.py`, `store.py`, `reducer.py`, `timeline.py`, `diffs.py`, `federation.py`, `reply.py`, `tui.py`, `ingest/hooks.py`, `ingest/tmux.py`, `ingest/transcripts.py` |

## Entry points

- **`argusd = argus.cli:daemon_main`** — daemon: `run` (serve, STUB) and
  `install-hooks` (IMPLEMENTED) subcommands.
- **`argus = argus.cli:tui_main`** — TUI: loads config, launches `ArgusApp`
  (run body STUB).
- **`argus.daemon.create_app(config) -> FastAPI`** — app factory the daemon
  serves (mounts hooks router, SSE `/api/state`, peer endpoints, lifespan tasks).

## Directory: `src/argus/` (core package)

| File | Key symbols | Notes |
|---|---|---|
| `__init__.py` | `__version__="0.1.0"`, `__all__` | Re-exports the models+config contract as the public `argus` surface. |
| `models.py` ✅ | `SessionStatus`, `HookEvent`, `TimelineKind` (StrEnums); `utcnow()`; `Event` (frozen); `SessionSnapshot` (`.needs_you`, `.is_terminal`, `.label()`); `TimelineEntry`; `Buckets`; `FleetState` (`all_sessions`/`upsert`/`merge`/`bucketed`) | **Frozen contract.** No pydantic. `HookEvent` = 8 PascalCase lifecycle hooks. |
| `config.py` ✅ | `ARGUS_HOME`, `DEFAULT_CONFIG_PATH`; `NotifierKind`; `Thresholds`, `NotifierConfig`, `Paths`, `ArgusConfig`; `load_config(path)` | **Frozen contract.** stdlib `tomllib` loader, defaults-on-missing, per-key override. |
| `cli.py` ◐ | `HOOK_EVENTS`✅, `hook_command(port)`✅, `build_hook_block(port)`✱, `install_hooks(...)`✱, `build_parser()`✅, `daemon_main(argv)`, `tui_main(argv)` | `hook_command` = non-blocking curl POST to `/hook`. install-hooks path implemented; serve/TUI bodies stub. |
| `daemon.py` ✱ | `create_app(config) -> FastAPI`, `async state_stream(app)` | Wires store + hooks router + SSE `/api/state` + peer endpoints + lifespan background tasks. |
| `store.py` ✱ | `SessionStore(journal_path, machine)`: `append`, `get`, `snapshots`, `events_for`, `local_fleet`, `recover`, `close` | In-memory snapshot map + append-only SQLite journal; `recover()` replays through `reduce`. |
| `reducer.py` ✱ | `reduce(snapshot, event) -> SessionSnapshot`, `extract_question(event)`, `is_dead(snapshot, *, now, thresholds, pane_alive, last_jsonl_activity)` | **Pure, no I/O.** The state-machine fold + question extraction + dead detection. |
| `timeline.py` ✱ | `build_timeline(events, *, transcript_path)`, `parse_transcript_timeline(path)` | Collapsed semantic timeline rows (tools/files/tests/tokens/questions). |
| `diffs.py` ✱ | `DiffStat` (frozen), `DiffCache(ttl=3.0)`: `get(cwd)`, `invalidate(cwd)` | Read-only `git status`/`diff --stat` per session cwd, TTL-cached; non-git cwd → benign empty stat. |
| `federation.py` ✱ | `Federation(config)`: `push_event`, `exchange`, `run`, `merge_remote` | Full-mesh state exchange over httpx; periodic full-state push + LWW merge. |
| `notify.py` ◐ | `Digest`+`.render()`✅, `Notifier` (Protocol), `NoopNotifier`✅, `WhatsAppNotifier`✱, `NotifyBatcher`✱ | Digest render + Noop done; WhatsApp subprocess + throttled batcher stub. |
| `reply.py` ✱ | `ReplyOutcome` (SENT/STALE/NO_PANE/ERROR), `Result`, `guarded_send(session, text, expected_prompt, *, socket)` | Guarded tmux keystroke injection — re-verify prompt still on screen before sending, else STALE. |

### Subdirectory: `src/argus/ingest/` (the three state sources)

| File | Key symbols | Notes |
|---|---|---|
| `__init__.py` | (docstring only) | The three sources: hooks / transcripts / tmux, each producing `Event`s. |
| `hooks.py` ✱ | `router` (APIRouter), `parse_hook_body(body, *, machine)`, `POST /hook receive_hook` | Turns Claude Code hook POSTs into `Event`s; mounted by daemon. |
| `tmux.py` ✱ | `Pane`; `list_panes`, `capture_pane`, `is_pane_alive`, `detect_prompt(pane_text)`, `send_keys` | subprocess tmux wrappers, `socket`-parameterised for testability. |
| `transcripts.py` ✱ | `parse_transcript_line`, `parse_transcript_file(path, *, machine)`, `async watch_transcripts(roots, *, machine)` | Tail `~/.claude/projects/**/*.jsonl` into `Event`s (backfill/catch-all path). |

### TUI

| File | Key symbols | Notes |
|---|---|---|
| `tui.py` ✱ | `ArgusApp(App[None])`: `BINDINGS`✅ (j/k, y/n, enter, q), `__init__`, `compose`, `on_mount` (open SSE), `action_reply_yes/no/attach` | Textual departures-board; BINDINGS data is real, method bodies stub. |

*Legend: ✅ implemented · ◐ partial · ✱ stub*

## Directory: `tests/`

MUST-pass tests cover the frozen contract; intended-behavior tests for stubs are
`@pytest.mark.xfail(reason="stub", strict=False)` so they flip to XPASS as each
module lands. `asyncio_mode=auto`.

| File | Covers | Kind |
|---|---|---|
| `conftest.py` | `FIXTURES`; `fixtures_dir`, `clean_transcript`, `blocked_transcript`, `tool_heavy_transcript`, `tmux_server` (real isolated tmux on per-pid socket; skips if tmux absent) | fixtures |
| `test_config.py` | `load_config` defaults / partial override / invalid kind raises / str-path | **must pass** |
| `test_models.py` | enum values, frozen `Event`, snapshot label+flags, fleet upsert/merge/bucketed order | **must pass** |
| `test_cli.py` | HOOK_EVENTS, `hook_command`, `build_parser` (pass); `install_hooks`/`build_hook_block` (xfail) | mixed |
| `test_notify.py` | `Digest.render` + `NoopNotifier` (pass); WhatsApp/batcher (xfail) | mixed |
| `test_reducer.py` | state-machine transitions + `is_dead` | xfail |
| `test_property_reducer.py` | Hypothesis invariants (valid status, monotonic `updated_at`, `tool_name` iff TOOL) | xfail |
| `test_daemon.py` | `create_app` returns FastAPI with `/hook` + `/api/state` | xfail |
| `test_store.py` | append/get roundtrip + journal survives restart via `recover()` | xfail |
| `test_timeline.py` | `parse_transcript_timeline` rows / question / counts / missing-file | xfail |
| `test_diffs.py` | `DiffCache` reports working-tree changes; non-git benign | xfail |
| `test_federation.py` | `exchange` / `merge_remote` merge peer machines | xfail |
| `test_reply.py` | `guarded_send` sends when live / refuses (STALE) when stale | xfail |
| `test_ingest_hooks.py` | `parse_hook_body` extracts tool; missing field raises KeyError | xfail |
| `test_ingest_tmux.py` | tmux wrappers vs real `tmux_server`; `detect_prompt` heuristic | xfail |
| `test_ingest_transcripts.py` | `parse_transcript_file` ordered events / notification / missing-file | xfail |
| `test_tui.py` | `BINDINGS` cover board controls (active); constructs from config (xfail) | mixed |

**`tests/fixtures/`** — synthetic (non-real) JSONL transcripts, documented in
`fixtures/README.md`: `clean_session.jsonl` (start → Read+Edit → done),
`blocked_session.jsonl` (permission/notification prompt → blocked, db-migration
question), `tool_heavy_session.jsonl` (tool-dense, token accumulation). No real
paths/tokens/messages.

## Directory: root (docs + packaging)

| File | Role |
|---|---|
| `pyproject.toml` | hatchling build, Python **>=3.14**. Runtime deps: fastapi, uvicorn[standard], watchfiles, textual, httpx, sse-starlette, tomli-w. Dev: pytest, pytest-asyncio, hypothesis, ruff. Console scripts `argusd`/`argus`. `pytest` `asyncio_mode=auto`, `testpaths=[tests]`. |
| `SHARED_INTERFACES.md` | The integration contract — precise public API of every module. Authoritative seam doc for implementers. |
| `DESIGN.md` | Design record: state machine, hybrid state source (#1), guarded injection (#5), full-mesh federation (#6), WhatsApp notifier (#7), read-only diffs (#9), UI/board layout, v1 acceptance. |
| `README.md` | What Argus is, departures-board TUI sketch, usage. |
| `LICENSE` | MIT. |

## Dependency sketch

```
__init__  ─► config, models              (public surface = the contract)

models    ─► (stdlib only: dataclasses, datetime, StrEnum)      [frozen]
config    ─► (stdlib only: tomllib, dataclasses, StrEnum, socket) [frozen]

reducer   ─► config.Thresholds, models                           [pure]
store     ─► models, reducer (reduce), sqlite/pathlib
timeline  ─► models
diffs     ─► (dataclasses, pathlib, git subprocess)
federation─► config.ArgusConfig, models, httpx
notify    ─► config.NotifierConfig, models
reply     ─► models.SessionSnapshot, ingest.tmux

ingest/hooks       ─► models.Event, fastapi.APIRouter
ingest/tmux        ─► subprocess, dataclasses
ingest/transcripts ─► models.Event, watchfiles

daemon    ─► config, store, reducer, ingest.hooks(router), federation, notify, fastapi/sse-starlette
cli       ─► config, models, argparse   (argusd/argus entry points)
tui       ─► config.ArgusConfig, models, textual, httpx(SSE client)
```

**Layering:** `models` + `config` are the leaf contract everyone imports.
`reducer` is pure and sits above them. `store` composes `reducer`. `daemon` is
the top-level composition root (store + ingest + federation + notify + SSE).
`cli` and `tui` are the two user-facing entry points. Nothing imports "up" into
`daemon`/`cli`/`tui`.
