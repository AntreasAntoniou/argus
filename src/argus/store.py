"""In-memory session store backed by a SQLite event journal.

The in-memory ``dict[session_id, SessionSnapshot]`` is the hot path the SSE API
reads; the append-only SQLite journal of every :class:`~argus.models.Event` lets
the daemon rebuild snapshots after a restart (``DESIGN.md`` §Components:
"in-memory + SQLite journal (survive daemon restart, feed timelines)"). The
journal path comes from :attr:`argus.config.Paths.journal_path`.

Recovery replays journalled events through :func:`argus.reducer.reduce` in
timestamp order to reconstruct the last-known snapshots.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from argus.models import Event, FleetState, SessionSnapshot
from argus.reducer import reduce

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT    NOT NULL,
    machine         TEXT    NOT NULL,
    hook_event_name TEXT    NOT NULL,
    ts              TEXT    NOT NULL,
    cwd             TEXT,
    tool_name       TEXT,
    tool_input      TEXT,
    raw             TEXT,
    branch          TEXT,
    tokens          INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_events_session
    ON events (session_id, ts, seq);
CREATE INDEX IF NOT EXISTS idx_events_order
    ON events (ts, seq);
"""

# Columns added after the initial release; applied idempotently on open so an
# existing journal upgrades in place rather than erroring on the new SELECTs.
_MIGRATIONS = (
    "ALTER TABLE events ADD COLUMN branch TEXT",
    "ALTER TABLE events ADD COLUMN tokens INTEGER NOT NULL DEFAULT 0",
)


class SessionStore:
    """Hot in-memory snapshot map plus a durable SQLite event journal.

    The in-memory map is the source of truth for live rendering; the journal is
    the durable log used for restart recovery and as the raw feed for
    :func:`argus.timeline.build_timeline`.
    """

    def __init__(self, journal_path: Path, machine: str) -> None:
        """Open (creating if needed) the SQLite journal and init in-memory state.

        Args:
            journal_path: Path to the SQLite journal file
                (:attr:`argus.config.Paths.journal_path`). Parent dirs are
                created if absent.
            machine: This node's hostname, stamped onto locally-sourced events
                and used to bucket snapshots into :class:`FleetState`.
        """

        self.journal_path = Path(journal_path)
        self.machine = machine
        self._snapshots: dict[str, SessionSnapshot] = {}

        self.journal_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.journal_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Apply idempotent column additions to an older journal file."""

        for statement in _MIGRATIONS:
            try:
                self._conn.execute(statement)
            except sqlite3.OperationalError:
                # Column already present — the table was created fresh from the
                # current _SCHEMA, so the ALTER is a no-op.
                pass

    def append(self, event: Event) -> SessionSnapshot:
        """Journal an event and fold it into the live snapshot for its session.

        Appends ``event`` to the SQLite journal, loads-or-creates the session's
        snapshot, runs :func:`argus.reducer.reduce`, stores the result, and
        returns the updated snapshot for immediate SSE broadcast.

        Args:
            event: The observation to record.

        Returns:
            The session's updated :class:`SessionSnapshot`.
        """

        self._conn.execute(
            "INSERT INTO events "
            "(session_id, machine, hook_event_name, ts, cwd, tool_name, "
            "tool_input, raw, branch, tokens) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.session_id,
                event.machine,
                event.hook_event_name,
                event.ts.isoformat(),
                event.cwd,
                event.tool_name,
                _dump(event.tool_input),
                _dump(_slim_raw(event.raw)),
                event.branch,
                event.tokens,
            ),
        )
        self._conn.commit()

        snapshot = reduce(self._snapshots.get(event.session_id), event)
        self._snapshots[event.session_id] = snapshot
        return snapshot

    def get(self, session_id: str) -> SessionSnapshot | None:
        """Return the live snapshot for ``session_id``, or ``None`` if unknown."""

        return self._snapshots.get(session_id)

    def snapshots(self) -> list[SessionSnapshot]:
        """Return all live snapshots on this machine."""

        return list(self._snapshots.values())

    def events_for(self, session_id: str) -> list[Event]:
        """Return all journalled events for a session, oldest first.

        Feeds :func:`argus.timeline.build_timeline`.
        """

        rows = self._conn.execute(
            "SELECT session_id, machine, hook_event_name, ts, cwd, tool_name, "
            "tool_input, raw, branch, tokens FROM events "
            "WHERE session_id = ? ORDER BY ts, seq",
            (session_id,),
        ).fetchall()
        return [_row_to_event(row) for row in rows]

    def local_fleet(self) -> FleetState:
        """Return a single-machine :class:`FleetState` of this node's snapshots."""

        return FleetState(machines={self.machine: self.snapshots()})

    def recover(self) -> None:
        """Rebuild the in-memory snapshot map by replaying the journal.

        Called on daemon startup: replays journalled events in timestamp order
        through :func:`argus.reducer.reduce` so state survives a restart.
        """

        self._snapshots.clear()
        rows = self._conn.execute(
            "SELECT session_id, machine, hook_event_name, ts, cwd, tool_name, "
            "tool_input, raw, branch, tokens FROM events ORDER BY ts, seq"
        ).fetchall()
        for row in rows:
            event = _row_to_event(row)
            self._snapshots[event.session_id] = reduce(
                self._snapshots.get(event.session_id), event
            )

    def prune(self, *, now: datetime, keep_within_seconds: int) -> int:
        """Drop long-quiet sessions from the in-memory map (journal untouched).

        Keeps the hot snapshot map — and therefore the federation wire and the
        liveness sweep — proportional to the live fleet rather than to weeks of
        history replayed by :meth:`recover`. BLOCKED sessions are always kept
        (they need the human however long they have waited); everything quiet
        longer than ``keep_within_seconds`` is evicted. Returns the count removed.

        A non-positive ``keep_within_seconds`` prunes nothing.
        """

        if keep_within_seconds <= 0:
            return 0
        cutoff = now - timedelta(seconds=keep_within_seconds)
        drop = [
            sid
            for sid, snap in self._snapshots.items()
            if not snap.needs_you and snap.updated_at < cutoff
        ]
        for sid in drop:
            del self._snapshots[sid]
        return len(drop)

    def compact(self) -> tuple[int, int]:
        """Slim every stored ``raw`` payload in place and reclaim file space.

        Rewrites existing rows through :func:`_slim_raw` (older journals stored
        the full transcript line per event) then ``VACUUM``s. Idempotent. Returns
        ``(rows_rewritten, bytes_reclaimed)``.
        """

        size_before = (
            self.journal_path.stat().st_size if self.journal_path.exists() else 0
        )
        rows = self._conn.execute(
            "SELECT seq, raw FROM events WHERE raw IS NOT NULL"
        ).fetchall()
        rewritten = 0
        for row in rows:
            slim = _dump(_slim_raw(_load(row["raw"])))
            if slim != row["raw"]:
                self._conn.execute(
                    "UPDATE events SET raw = ? WHERE seq = ?", (slim, row["seq"])
                )
                rewritten += 1
        self._conn.commit()
        self._conn.execute("VACUUM")
        self._conn.commit()
        size_after = (
            self.journal_path.stat().st_size if self.journal_path.exists() else 0
        )
        return rewritten, max(0, size_before - size_after)

    def close(self) -> None:
        """Flush and close the SQLite connection."""

        self._conn.commit()
        self._conn.close()


# The only keys anything downstream reads back out of a journalled ``raw``: the
# BLOCKED question (reducer.extract_question + timeline). Everything else in a
# transcript line — message.content, thinking blocks, tool payloads — is dead
# weight in the journal (it drove a 600MB+ file for ~70k events). We keep only
# these, and only when they are non-empty strings, so recover still rebuilds the
# question but the journal stays proportional to signal, not to transcript size.
_KEEP_RAW_KEYS = ("notification", "message", "prompt", "question", "body")


def _slim_raw(raw: dict | None) -> dict | None:
    """Reduce a raw payload to just the question-bearing string keys."""

    if not raw:
        return None
    kept = {
        key: value
        for key in _KEEP_RAW_KEYS
        if isinstance((value := raw.get(key)), str) and value.strip()
    }
    return kept or None


def _dump(payload: dict | None) -> str | None:
    """Serialize a JSON payload column, preserving ``None``."""

    return None if payload is None else json.dumps(payload)


def _load(text: str | None) -> dict | None:
    """Deserialize a JSON payload column, preserving ``None``."""

    return None if text is None else json.loads(text)


def _row_to_event(row: sqlite3.Row) -> Event:
    """Reconstruct an :class:`Event` from a journal row."""

    keys = row.keys()
    return Event(
        session_id=row["session_id"],
        machine=row["machine"],
        hook_event_name=row["hook_event_name"],
        ts=datetime.fromisoformat(row["ts"]),
        cwd=row["cwd"],
        tool_name=row["tool_name"],
        tool_input=_load(row["tool_input"]),
        raw=_load(row["raw"]),
        branch=row["branch"] if "branch" in keys else None,
        tokens=row["tokens"] if "tokens" in keys else 0,
    )
