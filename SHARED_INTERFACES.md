# SHARED_INTERFACES.md — the Argus integration contract

Precise public API of every module, so independent implementers code to the same
seams. **`models.py` and `config.py` are fully implemented** (do not change their
signatures without amending this file and `DESIGN.md`). Everything else is a
typed stub raising `NotImplementedError`; implement the body to satisfy the
contract below and its `tests/test_<module>.py`.

Conventions: full absolute imports (`from argus.models import Event`); dataclasses
+ enums (no pydantic); tz-aware UTC via `argus.models.utcnow()`; no I/O in
`reduce`; runtime state/secrets under `~/.argus/` only.

---

## `argus.models` — domain contract (IMPLEMENTED)

- `class SessionStatus(StrEnum)` — `STARTING="starting"`, `THINKING="thinking"`,
  `TOOL="tool"`, `BLOCKED="blocked"`, `IDLE="idle"`, `DONE="done"`, `DEAD="dead"`.
- `class HookEvent(StrEnum)` — the 8 lifecycle hooks: `SESSION_START`,
  `USER_PROMPT_SUBMIT`, `PRE_TOOL_USE`, `POST_TOOL_USE`, `NOTIFICATION`, `STOP`,
  `SUBAGENT_STOP`, `SESSION_END` (values are the PascalCase hook names).
- `class TimelineKind(StrEnum)` — `TOOL`, `FILE`, `TEST`, `TOKENS`, `QUESTION`,
  `LIFECYCLE`.
- `utcnow() -> datetime` — tz-aware UTC now. Use everywhere for timestamps.
- `@dataclass(frozen=True, slots=True) Event` — `session_id:str`, `machine:str`,
  `hook_event_name:str`, `ts:datetime=utcnow`, `cwd:str|None`, `tool_name:str|None`,
  `tool_input:dict|None`, `raw:dict|None`. Immutable observation from any ingester.
- `@dataclass(slots=True) SessionSnapshot` — `session_id`, `machine`,
  `status:SessionStatus=STARTING`, `question:str|None`, `cwd:str|None`,
  `branch:str|None`, `tokens:int=0`, `last_tool:str|None`, `updated_at:datetime`,
  `tool_name:str|None`. Props: `needs_you:bool` (BLOCKED), `is_terminal:bool`
  (DONE/DEAD); `label()->str` renders `tool:<name>` for TOOL else the status.
- `@dataclass(frozen=True, slots=True) TimelineEntry` — `ts`, `kind:TimelineKind`,
  `summary:str`, `detail:str=""`, `added_lines:int=0`, `removed_lines:int=0`.
- `@dataclass(frozen=True, slots=True) Buckets` — `needs_you`, `working`,
  `quiet`: `list[SessionSnapshot]`.
- `@dataclass(slots=True) FleetState` — `machines: dict[str, list[SessionSnapshot]]`.
  - `all_sessions() -> list[SessionSnapshot]` — flatten.
  - `upsert(snapshot) -> None` — insert/replace by `session_id` under its machine.
  - `merge(other) -> None` — replace each remote machine's list wholesale (LWW per machine).
  - `bucketed() -> Buckets` — BLOCKED→needs_you (oldest first), STARTING/THINKING/
    TOOL→working (newest first), IDLE/DONE/DEAD→quiet (newest first).

## `argus.config` — configuration (IMPLEMENTED)

- `ARGUS_HOME = ~/.argus`; `DEFAULT_CONFIG_PATH = ~/.argus/config.toml`.
- `class NotifierKind(StrEnum)` — `WHATSAPP="whatsapp"`, `NOOP="noop"`.
- `@dataclass Thresholds` — `dead_after_seconds:int=15`,
  `jsonl_silent_seconds:int=45`, `notify_batch_seconds:int=300`.
- `@dataclass NotifierConfig` — `kind:NotifierKind=NOOP`,
  `whatsapp_command:str` (template with `{message}`).
- `@dataclass Paths` — `claude_projects_root:Path=~/.claude/projects`,
  `journal_path:Path=~/.argus/journal.sqlite3`,
  `settings_path:Path=~/.claude/settings.json`.
- `@dataclass ArgusConfig` — `machine:str=hostname`, `peers:list[str]=[]`
  (`host:port`), `daemon_port:int=8787`, `thresholds`, `notifier`, `paths`.
- `load_config(path=DEFAULT_CONFIG_PATH) -> ArgusConfig` — stdlib `tomllib`;
  missing file → all defaults; partial file overrides only specified keys;
  invalid `notifier.kind` raises `ValueError`.

---

## `argus.store` — snapshot store + SQLite journal (STUB)

- `class SessionStore:`
  - `__init__(journal_path: Path, machine: str)` — open/create SQLite, init map.
  - `append(event: Event) -> SessionSnapshot` — journal + reduce + update + return.
  - `get(session_id: str) -> SessionSnapshot | None`.
  - `snapshots() -> list[SessionSnapshot]`.
  - `events_for(session_id: str) -> list[Event]` — oldest first (feeds timeline).
  - `local_fleet() -> FleetState` — this machine's snapshots wrapped.
  - `recover() -> None` — replay journal through `reduce` on startup.
  - `close() -> None`.

## `argus.reducer` — state machine (STUB, pure — no I/O)

- `reduce(snapshot: SessionSnapshot | None, event: Event) -> SessionSnapshot` —
  `None`→fresh STARTING; mapping: SessionStart→STARTING, UserPromptSubmit→THINKING,
  PreToolUse→TOOL(+tool_name/last_tool), PostToolUse→THINKING(clear tool_name,
  keep last_tool), Notification→BLOCKED(+question), Stop/SubagentStop→IDLE,
  SessionEnd→DONE. Never regress out of DONE/DEAD except a new SessionStart. Always
  stamp `updated_at=event.ts`, refresh `cwd` when present.
- `extract_question(event: Event) -> str | None` — pull prompt from Notification.
- `is_dead(snapshot, *, now, thresholds, pane_alive, last_jsonl_activity) -> bool`
  — pane gone OR jsonl silent past `thresholds.jsonl_silent_seconds`, while status ≠ DONE.

## `argus.timeline` — semantic timeline (STUB)

- `build_timeline(events, *, transcript_path=None) -> list[TimelineEntry]` — merge
  journal events + JSONL into ordered rows (asc by ts).
- `parse_transcript_timeline(transcript_path: Path) -> list[TimelineEntry]` —
  one-shot JSONL parse (drives fixture tests); `FileNotFoundError` if absent.

## `argus.ingest.hooks` — hook receiver (STUB)

- `router: fastapi.APIRouter` — mounted by `daemon.create_app`; exposes `POST /hook`.
- `parse_hook_body(body: dict, *, machine: str) -> Event` — map hook JSON to Event;
  `KeyError` if `hook_event_name`/`session_id` missing.
- `async receive_hook(body: dict) -> dict[str,str]` — parse + `store.append` +
  broadcast; returns `{"status":"ok"}`.

## `argus.ingest.transcripts` — JSONL watcher (STUB)

- `parse_transcript_line(line: dict, *, machine: str) -> Event | None` — one line
  → Event or None (skip mode/permission-mode/file-history-snapshot).
- `parse_transcript_file(path: Path, *, machine="local") -> list[Event]` —
  side-effect-free; file order; `FileNotFoundError` if absent.
- `async watch_transcripts(roots: Iterable[Path], *, machine) -> AsyncIterator[Event]`
  — `watchfiles.awatch`, tail new lines, track per-file offsets.

## `argus.ingest.tmux` — poller/reply channel (STUB)

- `@dataclass(frozen) Pane` — `pane_id`, `session_name`, `window_index:int`,
  `title`, `pid:int|None`.
- `list_panes(*, socket=None) -> list[Pane]` — `FileNotFoundError` if no tmux binary.
- `capture_pane(pane_id, *, socket=None, lines=200) -> str`.
- `is_pane_alive(pane_id, *, socket=None) -> bool`.
- `detect_prompt(pane_text: str) -> str | None` — the pending question or None.
- `send_keys(pane_id, text, *, socket=None, enter=True) -> None` — low-level; prefer
  `reply.guarded_send`.

## `argus.reply` — guarded injection (STUB)

- `class ReplyOutcome(StrEnum)` — `SENT`, `STALE`, `NO_PANE`, `ERROR`.
- `@dataclass(frozen) Result` — `outcome:ReplyOutcome`, `detail:str`,
  `observed_prompt:str|None`.
- `guarded_send(session, text, expected_prompt, *, socket=None) -> Result` —
  capture → verify `expected_prompt` still on screen → send-keys; else `STALE`.

## `argus.federation` — mesh (STUB)

- `class Federation:`
  - `__init__(config: ArgusConfig)`.
  - `async push_event(event: Event) -> None` — fan out to peers, never raise.
  - `async exchange(local: FleetState) -> FleetState` — swap full state, merge.
  - `async run(get_local, interval_seconds=2.0) -> None` — periodic loop.
  - `async merge_remote(remote: FleetState) -> None`.

## `argus.notify` — push (Noop IMPLEMENTED; WhatsApp/batcher STUB)

- `@dataclass(frozen) Digest` — `blocked:list[SessionSnapshot]`, `critical:bool=False`;
  `render() -> str` (count + per-agent question). **Implemented.**
- `class Notifier(Protocol)` — `send(digest: Digest) -> bool` (never raise on failure).
- `class NoopNotifier` — `send` logs, records to `.sent`, returns True. **Implemented.**
- `class WhatsAppNotifier(config: NotifierConfig)` — `send` substitutes `{message}`
  into `whatsapp_command`, subprocess-runs it; True iff exit 0. **Stub.**
- `@dataclass NotifyBatcher` — `notifier`, `batch_seconds=300`, `last_sent_at`;
  `observe_blocked(session) -> None`; `maybe_flush(*, now=None, critical=False) -> bool`
  (≤1 push / batch_seconds unless critical; dedupe by session_id). **Stub.**

## `argus.diffs` — read-only git (STUB)

- `@dataclass(frozen) DiffStat` — `branch:str|None`, `files_changed:int`,
  `added_lines:int`, `removed_lines:int`, `dirty:bool`, `detail:str`.
- `@dataclass DiffCache` — `ttl_seconds:float=3.0`; `get(cwd: Path) -> DiffStat`
  (non-git cwd → benign empty stat, never raises); `invalidate(cwd=None) -> None`.

## `argus.daemon` — app factory (STUB)

- `create_app(config: ArgusConfig) -> FastAPI` — wire store (recover), hooks router,
  SSE `GET /api/state`, peer endpoints, lifespan-managed watcher/poller/federation/
  notifier tasks.
- `async state_stream(app) -> AsyncIterator` — yield SSE `FleetState` events on change.

## `argus.tui` — Textual board (STUB)

- `class ArgusApp(App[None])` — `BINDINGS` real (j/k, y/n, enter, q **implemented data**);
  `__init__(config: ArgusConfig)`; `compose() -> ComposeResult`; `async on_mount()`
  (open SSE); `action_reply_yes/no()`; `action_attach()`.

## `argus.cli` — entry points (argparse + hook_command IMPLEMENTED; merge STUB)

- `HOOK_EVENTS: tuple[HookEvent, ...]` — the 8 events. **Implemented data.**
- `hook_command(port: int) -> str` — non-blocking curl POST to `/hook`. **Implemented.**
- `build_hook_block(port: int) -> dict` — per-event settings fragment. **Stub.**
- `install_hooks(settings_path, *, port, dry_run=False, backup=True) -> dict` —
  idempotent deep-merge into settings.json, `.bak` backup, dry-run returns without
  writing. **Stub.**
- `build_parser() -> argparse.ArgumentParser` — `argusd` with `run` / `install-hooks`
  subcommands. **Implemented.**
- `daemon_main(argv=None) -> int` — entry `argusd`; parses + `load_config`; dispatches
  install-hooks or serves. **Implemented except serve/merge bodies.**
- `tui_main(argv=None) -> int` — entry `argus`; parses + `load_config` + runs `ArgusApp`.
