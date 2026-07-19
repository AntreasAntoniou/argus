"""Federation — full-mesh state exchange across machines.

Implements ``DESIGN.md`` decision #6 (Full mesh). Every node runs an identical
``argusd``; the static peer list lives in
:attr:`argus.config.ArgusConfig.peers`. Peers (a) receive immediate event pushes
(``POST /peer/event``) and (b) exchange full local :class:`FleetState` on an
interval (``POST /peer/state``). Any node merges remote state (tagged by
machine) into its own aggregate view to render the whole fleet. At N≈4 this is
symmetric state exchange over ``httpx`` — deliberately boring, not a gossip
protocol.

Merge is last-write-wins per session by :attr:`SessionSnapshot.updated_at`, so a
peer echoing back a stale copy of another machine's session can never clobber a
fresher local observation.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any

import httpx

from argus.config import ArgusConfig
from argus.models import Event, FleetState, SessionSnapshot, SessionStatus

log = logging.getLogger(__name__)


class Federation:
    """Manages peer connections and fleet-state merge for one node."""

    def __init__(
        self, config: ArgusConfig, *, client: httpx.AsyncClient | None = None
    ) -> None:
        """Initialise with the static peer list from config.

        Args:
            config: This node's config; ``config.peers`` is the ``host:port``
                mesh and ``config.machine`` is this node's tag.
            client: Optional injected httpx client (tests pass one backed by a
                mock transport so no live network is touched). Defaults to a
                fresh :class:`httpx.AsyncClient`.
        """

        self.config = config
        self.machine = config.machine
        self._peers = list(config.peers)
        self._client = client if client is not None else httpx.AsyncClient(timeout=2.0)
        # Running merged view of self + every peer, folded LWW-by-updated_at.
        self._aggregate = FleetState()

    async def push_event(self, event: Event) -> None:
        """Fan an event out to every peer immediately (fire-and-forget).

        Failures to individual peers are logged, never raised — a down peer must
        not stall local ingestion.
        """

        payload = self.dump_event(event)

        async def _one(peer: str) -> None:
            try:
                resp = await self._client.post(
                    f"http://{peer}/peer/event", json=payload
                )
                resp.raise_for_status()
            except Exception as exc:  # noqa: BLE001 — a down peer must never raise
                log.warning("push_event to %s failed: %s", peer, exc)

        await asyncio.gather(*(_one(peer) for peer in self._peers))

    async def exchange(self, local: FleetState) -> FleetState:
        """Run one round of full-state exchange and return the merged fleet view.

        POSTs ``local`` to each peer's ``/peer/state`` endpoint, collects their
        :class:`FleetState`, and folds every machine into the running aggregate
        last-write-wins by :attr:`SessionSnapshot.updated_at`.

        Args:
            local: This node's current single-or-multi-machine fleet state.

        Returns:
            A merged :class:`FleetState` spanning self + all reachable peers.
        """

        _merge_lww(self._aggregate, local)
        payload = self.dump_fleet(local)

        async def _one(peer: str) -> FleetState | None:
            try:
                resp = await self._client.post(
                    f"http://{peer}/peer/state", json=payload
                )
                resp.raise_for_status()
                return self.load_fleet(resp.json())
            except Exception as exc:  # noqa: BLE001 — a down peer must never raise
                log.warning("exchange with %s failed: %s", peer, exc)
                return None

        for remote in await asyncio.gather(*(_one(peer) for peer in self._peers)):
            if remote is not None:
                _merge_lww(self._aggregate, remote)
        return self._aggregate

    async def run(
        self,
        get_local: Callable[[], FleetState],
        interval_seconds: float = 2.0,
    ) -> None:
        """Run the periodic exchange loop until cancelled.

        Args:
            get_local: A zero-arg callable returning the current local
                :class:`FleetState` (typically ``store.local_fleet``).
            interval_seconds: Seconds between exchange rounds.
        """

        while True:
            try:
                await self.exchange(get_local())
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — one bad round must not kill the loop
                log.warning("federation exchange round failed: %s", exc)
            await asyncio.sleep(interval_seconds)

    async def merge_remote(self, remote: FleetState) -> None:
        """Merge a peer-pushed :class:`FleetState` into the local aggregate view."""

        _merge_lww(self._aggregate, remote)

    # -- serialization (JSON over the wire; models stay pydantic-free) ---------

    @staticmethod
    def dump_event(event: Event) -> dict[str, Any]:
        """Serialize an :class:`Event` to a JSON-safe dict for ``/peer/event``."""

        return {
            "session_id": event.session_id,
            "machine": event.machine,
            "hook_event_name": event.hook_event_name,
            "ts": event.ts.isoformat(),
            "cwd": event.cwd,
            "tool_name": event.tool_name,
            "tool_input": event.tool_input,
            "raw": event.raw,
            "branch": event.branch,
            "tokens": event.tokens,
        }

    @staticmethod
    def dump_fleet(fleet: FleetState) -> dict[str, Any]:
        """Serialize a :class:`FleetState` to a JSON-safe dict for ``/peer/state``."""

        return {
            "machines": {
                machine: [_dump_snapshot(s) for s in sessions]
                for machine, sessions in fleet.machines.items()
            }
        }

    @staticmethod
    def load_fleet(data: dict[str, Any]) -> FleetState:
        """Rebuild a :class:`FleetState` from a :meth:`dump_fleet` payload."""

        fleet = FleetState()
        for sessions in data.get("machines", {}).values():
            for raw in sessions:
                fleet.upsert(_load_snapshot(raw))
        return fleet


def _merge_lww(target: FleetState, source: FleetState) -> None:
    """Fold ``source`` into ``target``, keeping the newer snapshot per session.

    Last-write-wins by :attr:`SessionSnapshot.updated_at`. Ties resolve to
    ``source`` (the freshly-arrived view), which is harmless because equal
    timestamps carry equal state.
    """

    for sessions in source.machines.values():
        for snap in sessions:
            existing = _find(target, snap.machine, snap.session_id)
            if existing is None or snap.updated_at >= existing.updated_at:
                target.upsert(snap)


def _find(fleet: FleetState, machine: str, session_id: str) -> SessionSnapshot | None:
    for snap in fleet.machines.get(machine, ()):
        if snap.session_id == session_id:
            return snap
    return None


def _dump_snapshot(snap: SessionSnapshot) -> dict[str, Any]:
    return {
        "session_id": snap.session_id,
        "machine": snap.machine,
        "status": snap.status.value,
        "question": snap.question,
        "cwd": snap.cwd,
        "branch": snap.branch,
        "tokens": snap.tokens,
        "last_tool": snap.last_tool,
        "updated_at": snap.updated_at.isoformat(),
        "tool_name": snap.tool_name,
    }


def _load_snapshot(raw: dict[str, Any]) -> SessionSnapshot:
    return SessionSnapshot(
        session_id=raw["session_id"],
        machine=raw["machine"],
        status=SessionStatus(raw["status"]),
        question=raw.get("question"),
        cwd=raw.get("cwd"),
        branch=raw.get("branch"),
        tokens=int(raw.get("tokens", 0)),
        last_tool=raw.get("last_tool"),
        updated_at=datetime.fromisoformat(raw["updated_at"]),
        tool_name=raw.get("tool_name"),
    )
