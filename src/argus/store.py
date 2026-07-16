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
from datetime import datetime
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
    raw             TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_session
    ON events (session_id, ts, seq);
CREATE INDEX IF NOT EXISTS idx_events_order
    ON events (ts, seq);
"""


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
        self._conn.commit()

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
            "tool_input, raw) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.session_id,
                event.machine,
                event.hook_event_name,
                event.ts.isoformat(),
                event.cwd,
                event.tool_name,
                _dump(event.tool_input),
                _dump(event.raw),
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
            "tool_input, raw FROM events WHERE session_id = ? ORDER BY ts, seq",
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
            "tool_input, raw FROM events ORDER BY ts, seq"
        ).fetchall()
        for row in rows:
            event = _row_to_event(row)
            self._snapshots[event.session_id] = reduce(
                self._snapshots.get(event.session_id), event
            )

    def close(self) -> None:
        """Flush and close the SQLite connection."""

        self._conn.commit()
        self._conn.close()


def _dump(payload: dict | None) -> str | None:
    """Serialize a JSON payload column, preserving ``None``."""

    return None if payload is None else json.dumps(payload)


def _load(text: str | None) -> dict | None:
    """Deserialize a JSON payload column, preserving ``None``."""

    return None if text is None else json.loads(text)


def _row_to_event(row: sqlite3.Row) -> Event:
    """Reconstruct an :class:`Event` from a journal row."""

    return Event(
        session_id=row["session_id"],
        machine=row["machine"],
        hook_event_name=row["hook_event_name"],
        ts=datetime.fromisoformat(row["ts"]),
        cwd=row["cwd"],
        tool_name=row["tool_name"],
        tool_input=_load(row["tool_input"]),
        raw=_load(row["raw"]),
    )
