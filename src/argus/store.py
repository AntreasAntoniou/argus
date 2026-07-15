"""In-memory session store backed by a SQLite event journal.

STUB — precise typed contract only. Implementers: build the in-memory
``dict[session_id, SessionSnapshot]`` as the hot path the SSE API reads, and an
append-only SQLite journal of every :class:`~argus.models.Event` so the daemon
can rebuild snapshots after a restart (``DESIGN.md`` §Components: "in-memory +
SQLite journal (survive daemon restart, feed timelines)"). The journal path
comes from :attr:`argus.config.Paths.journal_path`.

Recovery replays journalled events through :func:`argus.reducer.reduce` in
timestamp order to reconstruct the last-known snapshots.
"""

from __future__ import annotations

from pathlib import Path

from argus.models import Event, FleetState, SessionSnapshot


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

        raise NotImplementedError(
            "Open SQLite journal (create schema if new), init in-memory snapshot map"
        )

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

        raise NotImplementedError("Insert into journal, reduce, update in-memory map")

    def get(self, session_id: str) -> SessionSnapshot | None:
        """Return the live snapshot for ``session_id``, or ``None`` if unknown."""

        raise NotImplementedError("Return in-memory snapshot for session_id")

    def snapshots(self) -> list[SessionSnapshot]:
        """Return all live snapshots on this machine."""

        raise NotImplementedError("Return all in-memory snapshots")

    def events_for(self, session_id: str) -> list[Event]:
        """Return all journalled events for a session, oldest first.

        Feeds :func:`argus.timeline.build_timeline`.
        """

        raise NotImplementedError("SELECT events WHERE session_id ORDER BY ts")

    def local_fleet(self) -> FleetState:
        """Return a single-machine :class:`FleetState` of this node's snapshots."""

        raise NotImplementedError("Wrap this machine's snapshots in a FleetState")

    def recover(self) -> None:
        """Rebuild the in-memory snapshot map by replaying the journal.

        Called on daemon startup: replays journalled events in timestamp order
        through :func:`argus.reducer.reduce` so state survives a restart.
        """

        raise NotImplementedError("Replay journal through reducer to rebuild snapshots")

    def close(self) -> None:
        """Flush and close the SQLite connection."""

        raise NotImplementedError("Close SQLite connection")
