"""Argus domain model — the shared contract every other module codes against.

This module is fully implemented and is the single source of truth for the
types that flow through the system: raw :class:`Event` objects produced by the
ingesters, the per-session :class:`SessionSnapshot` produced by the reducer,
the :class:`TimelineEntry` rows produced by the timeline builder, and the
fleet-wide :class:`FleetState` rendered by the TUI.

The state machine mirrors ``DESIGN.md`` exactly::

    starting -> thinking <-> tool:<name> -> blocked(question) -> ... -> done | dead

No pydantic: dataclasses + enums only, per the design record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class SessionStatus(StrEnum):
    """Explicit per-session state.

    The string values are the wire/JSONL representation used across machines and
    match the lowercase tokens in ``DESIGN.md``'s state machine. ``TOOL`` renders
    as ``tool:<name>`` in the UI, where ``<name>`` comes from
    :attr:`SessionSnapshot.tool_name`.
    """

    STARTING = "starting"
    THINKING = "thinking"
    TOOL = "tool"
    BLOCKED = "blocked"
    IDLE = "idle"
    DONE = "done"
    DEAD = "dead"


class HookEvent(StrEnum):
    """The eight Claude Code lifecycle hooks Argus subscribes to.

    Emitted by the async hook pack (``argus install-hooks``) as POSTs to
    ``/hook`` and parsed by :mod:`argus.ingest.hooks` into :class:`Event`.
    """

    SESSION_START = "SessionStart"
    USER_PROMPT_SUBMIT = "UserPromptSubmit"
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    NOTIFICATION = "Notification"
    STOP = "Stop"
    SUBAGENT_STOP = "SubagentStop"
    SESSION_END = "SessionEnd"


class TimelineKind(StrEnum):
    """The kind of a collapsed :class:`TimelineEntry` row."""

    TOOL = "tool"
    FILE = "file"
    TEST = "test"
    TOKENS = "tokens"
    QUESTION = "question"
    LIFECYCLE = "lifecycle"


def utcnow() -> datetime:
    """Return a timezone-aware UTC timestamp.

    Centralised so every module stamps events consistently (naive datetimes are
    a recurring source of ordering bugs across machines).
    """

    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class Event:
    """An immutable observation about a session from any ingestion path.

    Produced by hooks (:mod:`argus.ingest.hooks`), the transcript watcher
    (:mod:`argus.ingest.transcripts`), or the tmux poller
    (:mod:`argus.ingest.tmux`). Fed to :func:`argus.reducer.reduce` to advance
    the state machine, and appended to the SQLite journal by
    :class:`argus.store.SessionStore`.

    Attributes:
        session_id: Claude Code session UUID.
        machine: Hostname the session runs on (federation tag).
        hook_event_name: The lifecycle hook or a synthetic name from a
            non-hook ingester (e.g. ``"transcript"`` / ``"tmux.dead"``). Prefer
            :class:`HookEvent` values when the source is a real hook.
        ts: Timezone-aware event timestamp.
        cwd: Working directory of the session, if known.
        tool_name: Tool name for ``PreToolUse`` / ``PostToolUse`` events.
        tool_input: Parsed tool input payload for tool events.
        raw: The original payload (hook JSON body or parsed JSONL line) for
            audit and lossless replay.
        branch: Git branch of ``cwd`` at the time of the event, if the source
            reported one (transcript ``gitBranch`` / hook payload).
        tokens: Tokens attributable to this single event (an assistant turn's
            ``message.usage`` total); the reducer accumulates these onto the
            session snapshot. ``0`` for events with no usage.
    """

    session_id: str
    machine: str
    hook_event_name: str
    ts: datetime = field(default_factory=utcnow)
    cwd: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    raw: dict[str, Any] | None = None
    branch: str | None = None
    tokens: int = 0


@dataclass(slots=True)
class SessionSnapshot:
    """The current reduced state of one session.

    This is what the board renders and what the reducer mutates. It is mutable
    by design: :func:`argus.reducer.reduce` returns an updated snapshot per
    event. ``tool_name`` is only meaningful when ``status is SessionStatus.TOOL``.

    Attributes:
        session_id: Claude Code session UUID.
        machine: Hostname the session runs on.
        status: Current :class:`SessionStatus`.
        question: The exact pending question when ``status`` is ``BLOCKED``.
        cwd: Working directory of the session.
        branch: Git branch of ``cwd`` at last observation.
        tokens: Cumulative tokens burned (input + output) as last seen.
        last_tool: Name of the most recent tool used (persists across THINKING).
        updated_at: Timestamp of the last event that touched this snapshot.
        tool_name: Active tool name while ``status is TOOL``; ``None`` otherwise.
    """

    session_id: str
    machine: str
    status: SessionStatus = SessionStatus.STARTING
    question: str | None = None
    cwd: str | None = None
    branch: str | None = None
    tokens: int = 0
    last_tool: str | None = None
    updated_at: datetime = field(default_factory=utcnow)
    tool_name: str | None = None

    @property
    def needs_you(self) -> bool:
        """True when a human decision is required (``BLOCKED``)."""

        return self.status is SessionStatus.BLOCKED

    @property
    def is_terminal(self) -> bool:
        """True when the session will not advance further (``DONE`` / ``DEAD``)."""

        return self.status in (SessionStatus.DONE, SessionStatus.DEAD)

    def label(self) -> str:
        """Render the status as shown on the board (``tool:<name>`` for TOOL)."""

        if self.status is SessionStatus.TOOL and self.tool_name:
            return f"tool:{self.tool_name}"
        return str(self.status)


@dataclass(frozen=True, slots=True)
class TimelineEntry:
    """One collapsed row in a session's semantic timeline.

    Built by :func:`argus.timeline.build_timeline` from events + JSONL. ``detail``
    holds the expandable body; ``added_lines`` / ``removed_lines`` are populated
    for :attr:`TimelineKind.FILE` rows from diff-stat.

    Attributes:
        ts: When the entry occurred.
        kind: The :class:`TimelineKind` of this row.
        summary: One-line collapsed representation (always shown).
        detail: Expanded body (tool input, diff, test output); may be empty.
        added_lines: Lines added, for file rows.
        removed_lines: Lines removed, for file rows.
    """

    ts: datetime
    kind: TimelineKind
    summary: str
    detail: str = ""
    added_lines: int = 0
    removed_lines: int = 0


@dataclass(frozen=True, slots=True)
class Buckets:
    """Sessions sorted for the departures-board layout.

    ``needs_you`` pins to the top, ``working`` in the middle, ``quiet`` at the
    bottom — see ``DESIGN.md`` §UI and the README board sketch.
    """

    needs_you: list[SessionSnapshot]
    working: list[SessionSnapshot]
    quiet: list[SessionSnapshot]


# Which statuses fall into which board bucket. Single source of truth so the
# TUI, notifier, and tests agree.
_WORKING_STATUSES = frozenset(
    {SessionStatus.STARTING, SessionStatus.THINKING, SessionStatus.TOOL}
)
_QUIET_STATUSES = frozenset(
    {SessionStatus.IDLE, SessionStatus.DONE, SessionStatus.DEAD}
)


@dataclass(slots=True)
class FleetState:
    """Fleet-wide aggregation of every session on every machine.

    Keyed by machine hostname. Populated locally by the daemon and merged from
    peers by :mod:`argus.federation`. Any node can render the whole fleet from
    one of these.
    """

    machines: dict[str, list[SessionSnapshot]] = field(default_factory=dict)

    def all_sessions(self) -> list[SessionSnapshot]:
        """Flatten every machine's sessions into a single list."""

        return [s for sessions in self.machines.values() for s in sessions]

    def upsert(self, snapshot: SessionSnapshot) -> None:
        """Insert or replace a snapshot under its machine, keyed by session id."""

        bucket = self.machines.setdefault(snapshot.machine, [])
        for i, existing in enumerate(bucket):
            if existing.session_id == snapshot.session_id:
                bucket[i] = snapshot
                return
        bucket.append(snapshot)

    def merge(self, other: FleetState) -> None:
        """Merge a peer's fleet state in, replacing that peer's machines wholesale.

        Federation exchanges full per-machine state, so a remote machine's list
        is authoritative for that machine (last-writer-wins per machine key).
        """

        for machine, sessions in other.machines.items():
            self.machines[machine] = list(sessions)

    def bucketed(self) -> Buckets:
        """Sort all sessions into needs_you / working / quiet for the board.

        ``needs_you`` is ordered oldest-question-first (longest wait floats up);
        the other buckets are ordered most-recently-updated first.
        """

        needs_you: list[SessionSnapshot] = []
        working: list[SessionSnapshot] = []
        quiet: list[SessionSnapshot] = []
        for s in self.all_sessions():
            if s.status is SessionStatus.BLOCKED:
                needs_you.append(s)
            elif s.status in _WORKING_STATUSES:
                working.append(s)
            else:  # _QUIET_STATUSES (and any unforeseen status dims to quiet)
                quiet.append(s)
        needs_you.sort(key=lambda s: s.updated_at)
        working.sort(key=lambda s: s.updated_at, reverse=True)
        quiet.sort(key=lambda s: s.updated_at, reverse=True)
        return Buckets(needs_you=needs_you, working=working, quiet=quiet)
