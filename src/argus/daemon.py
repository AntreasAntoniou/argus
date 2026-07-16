"""argusd — the FastAPI daemon: ingesters, reducer, SSE, federation, notifier.

:func:`create_app` is the app factory that wires everything together
(``DESIGN.md`` §Components). It holds a :class:`~argus.store.SessionStore`,
mounts the hook router, exposes an SSE ``GET /api/state`` stream (plus a plain
``GET /api/state/snapshot`` for a one-shot read), registers the federation peer
endpoints, and — bound to the app lifespan — launches the JSONL watcher, the
tmux liveness poller, the federation exchange loop, and the notifier batcher as
background tasks that are cancelled on shutdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI, Request
from sse_starlette.sse import EventSourceResponse

from argus.config import ArgusConfig, NotifierKind
from argus.federation import Federation
from argus.ingest import tmux
from argus.ingest.hooks import router as hooks_router
from argus.ingest.transcripts import watch_transcripts
from argus.models import (
    Event,
    FleetState,
    SessionSnapshot,
    SessionStatus,
    utcnow,
)
from argus.notify import NoopNotifier, NotifyBatcher, WhatsAppNotifier
from argus.reducer import is_dead, reduce
from argus.store import SessionStore

log = logging.getLogger("argus.daemon")


def create_app(config: ArgusConfig) -> FastAPI:
    """Build and wire the ``argusd`` FastAPI application.

    Args:
        config: Loaded :class:`ArgusConfig` for this node.

    Returns:
        The configured :class:`fastapi.FastAPI` app (not yet served).
    """

    notifier = (
        WhatsAppNotifier(config.notifier)
        if config.notifier.kind is NotifierKind.WHATSAPP
        else NoopNotifier()
    )

    app = FastAPI(title="argusd", lifespan=_lifespan)
    app.state.config = config
    app.state.store = None  # created in lifespan (SQLite is bound to its thread)
    app.state.remote = FleetState()  # snapshots learned from peers, by machine
    app.state.federation = Federation(config)
    app.state.batcher = NotifyBatcher(
        notifier, batch_seconds=config.thresholds.notify_batch_seconds
    )
    app.state.subscribers = set()  # set[asyncio.Queue[str]] of SSE listeners

    async def broadcast(_snapshot: SessionSnapshot | None = None) -> None:
        """Push the current merged fleet to every live SSE subscriber."""

        data = _dump_current(app)
        for queue in list(app.state.subscribers):
            queue.put_nowait(data)

    app.state.broadcast = broadcast

    # Flatten the hook APIRouter onto the app so ``POST /hook`` appears as a
    # top-level route (FastAPI's lazy ``include_router`` otherwise hides it
    # behind an opaque router node that route introspection cannot see).
    app.router.routes.extend(hooks_router.routes)

    @app.get("/api/state")
    async def api_state(request: Request) -> EventSourceResponse:
        """Stream :class:`FleetState` over SSE, re-emitting on every change."""

        return EventSourceResponse(state_stream(request.app))

    @app.get("/api/state/snapshot")
    async def api_state_snapshot(request: Request) -> dict[str, Any]:
        """Return the current merged fleet as a one-shot JSON read."""

        return Federation.dump_fleet(_current_fleet(request.app))

    @app.post("/peer/event")
    async def peer_event(body: dict[str, Any], request: Request) -> dict[str, str]:
        """Fold a peer-pushed event into the remote view and broadcast."""

        event = _load_event(body)
        remote: FleetState = request.app.state.remote
        prior = _find(remote, event.machine, event.session_id)
        remote.upsert(reduce(prior, event))
        await request.app.state.broadcast()
        return {"status": "ok"}

    @app.post("/peer/state")
    async def peer_state(body: dict[str, Any], request: Request) -> dict[str, Any]:
        """Merge a peer's full :class:`FleetState` and echo back our own."""

        request.app.state.remote.merge(Federation.load_fleet(body))
        await request.app.state.broadcast()
        return Federation.dump_fleet(request.app.state.store.local_fleet())

    return app


async def state_stream(app: FastAPI) -> AsyncIterator[dict[str, str]]:
    """Yield SSE events as the fleet state changes.

    Registers a per-connection queue, emits the current state immediately (so a
    fresh TUI paints without waiting for the next change), then yields whenever
    :func:`create_app`'s ``broadcast`` fires.

    Args:
        app: The running application (for ``app.state`` access).

    Yields:
        ``sse-starlette`` event dicts (``{"event": "state", "data": <json>}``).
    """

    queue: asyncio.Queue[str] = asyncio.Queue()
    app.state.subscribers.add(queue)
    try:
        yield {"event": "state", "data": _dump_current(app)}
        while True:
            yield {"event": "state", "data": await queue.get()}
    finally:
        app.state.subscribers.discard(queue)


# -- fleet assembly / serialization -------------------------------------------


def _current_fleet(app: FastAPI) -> FleetState:
    """Combine this node's local snapshots with the peer-learned remote view."""

    fleet = FleetState()
    fleet.merge(app.state.store.local_fleet())
    fleet.merge(app.state.remote)
    return fleet


def _dump_current(app: FastAPI) -> str:
    """Serialize the current merged fleet to the SSE wire JSON string."""

    return json.dumps(Federation.dump_fleet(_current_fleet(app)))


def _find(fleet: FleetState, machine: str, session_id: str) -> SessionSnapshot | None:
    """Locate a snapshot in ``fleet`` by machine + session id, or ``None``."""

    for snap in fleet.machines.get(machine, ()):
        if snap.session_id == session_id:
            return snap
    return None


def _load_event(body: dict[str, Any]) -> Event:
    """Reconstruct an :class:`Event` from a :meth:`Federation.dump_event` body."""

    from datetime import datetime

    ts = body.get("ts")
    return Event(
        session_id=body["session_id"],
        machine=body["machine"],
        hook_event_name=body["hook_event_name"],
        ts=datetime.fromisoformat(ts) if ts else utcnow(),
        cwd=body.get("cwd"),
        tool_name=body.get("tool_name"),
        tool_input=body.get("tool_input"),
        raw=body.get("raw"),
    )


# -- liveness (pure, unit-testable without tmux/async) ------------------------


def mark_dead_sessions(
    store: SessionStore,
    *,
    now: Any,
    thresholds: Any,
    alive_session_ids: set[str],
) -> list[SessionSnapshot]:
    """Flip local sessions to ``DEAD`` when their pane is gone or JSONL is silent.

    Pure over the store's in-memory snapshots (no tmux, no I/O), so the tmux
    poller's decision logic is directly testable. Mutates the store's snapshots
    in place and returns the ones that changed.

    Args:
        store: The session store whose snapshots are evaluated.
        now: Current tz-aware time.
        thresholds: :class:`argus.config.Thresholds` liveness windows.
        alive_session_ids: Session ids currently backed by a live tmux pane.

    Returns:
        The snapshots transitioned to ``DEAD`` this pass.
    """

    changed: list[SessionSnapshot] = []
    for snap in store.snapshots():
        if snap.is_terminal:
            continue
        pane_alive = snap.session_id in alive_session_ids
        if is_dead(
            snap,
            now=now,
            thresholds=thresholds,
            pane_alive=pane_alive,
            last_jsonl_activity=snap.updated_at,
        ):
            snap.status = SessionStatus.DEAD
            snap.updated_at = now
            changed.append(snap)
    return changed


# -- lifespan background tasks ------------------------------------------------


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start ingesters/poller/federation/notifier; cancel them on shutdown."""

    config: ArgusConfig = app.state.config
    # Open the store here so its SQLite connection lives on the serving thread.
    store = SessionStore(config.paths.journal_path, config.machine)
    store.recover()
    app.state.store = store
    tasks = [
        asyncio.create_task(_run_transcripts(app), name="argus-transcripts"),
        asyncio.create_task(_run_tmux_poll(app), name="argus-tmux-poll"),
        asyncio.create_task(_run_notifier(app), name="argus-notifier"),
    ]
    if config.peers:
        federation: Federation = app.state.federation
        tasks.append(
            asyncio.create_task(
                federation.run(app.state.store.local_fleet), name="argus-federation"
            )
        )
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        with suppress(Exception):
            store.close()


async def _run_transcripts(app: FastAPI) -> None:
    """Tail JSONL transcripts, folding new events into the store."""

    config: ArgusConfig = app.state.config
    store: SessionStore = app.state.store
    try:
        async for event in watch_transcripts(
            [config.paths.claude_projects_root], machine=config.machine
        ):
            store.append(event)
            await app.state.broadcast()
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 — a watcher fault must not kill the daemon
        log.exception("transcript watcher stopped")


async def _run_tmux_poll(app: FastAPI) -> None:
    """Poll tmux for pane liveness and mark vanished sessions ``DEAD``."""

    config: ArgusConfig = app.state.config
    store: SessionStore = app.state.store
    interval = max(1, config.thresholds.poll_interval_seconds)
    while True:
        await asyncio.sleep(interval)
        try:
            panes = tmux.list_panes()
        except FileNotFoundError:
            # No tmux on this host — still sweep for dead-by-silence with no
            # pane evidence, so headless machines age out silent sessions too.
            panes = []
        except Exception:  # noqa: BLE001 — a bad poll must not kill the loop
            continue
        alive = {p.session_name for p in panes} | {p.title for p in panes}
        changed = mark_dead_sessions(
            store,
            now=utcnow(),
            thresholds=config.thresholds,
            alive_session_ids=alive,
        )
        if changed:
            await app.state.broadcast()


async def _run_notifier(app: FastAPI) -> None:
    """Coalesce blocked sessions into batched push digests."""

    store: SessionStore = app.state.store
    batcher: NotifyBatcher = app.state.batcher
    while True:
        await asyncio.sleep(5)
        for snap in store.snapshots():
            if snap.needs_you:
                batcher.observe_blocked(snap)
        batcher.maybe_flush()
