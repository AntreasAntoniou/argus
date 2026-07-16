"""Hook receiver — FastAPI router turning Claude Code hook POSTs into Events.

Mount :data:`router` on the daemon app. It exposes ``POST /hook`` accepting the
JSON body the async hook pack sends for each of the eight lifecycle events
(:class:`argus.models.HookEvent`). The body is parsed into an
:class:`argus.models.Event` and handed to the :class:`~argus.store.SessionStore`
pulled off ``request.app.state`` (the daemon wires it there at app-factory
time, so this module stays a leaf and never imports the store).

The hook body carries at least ``hook_event_name`` and ``session_id``, plus
``cwd`` and — for tool hooks — ``tool_name`` and ``tool_input``. ``Notification``
bodies carry the question/permission text (kept in ``raw`` for the reducer to
lift into ``BLOCKED``). Handlers respond ``2xx`` fast; the hooks are async and
must never block a session.
"""

from __future__ import annotations

import inspect
from typing import Any

from fastapi import APIRouter, Request

from argus.models import Event, utcnow

# Mounted by argus.daemon.create_app(). Route handlers pull the SessionStore
# off app.state (wired at app-factory time).
router = APIRouter(tags=["ingest"])


def parse_hook_body(body: dict[str, Any], *, machine: str) -> Event:
    """Convert a raw hook POST body into an :class:`Event`.

    Args:
        body: The decoded JSON body of a ``POST /hook`` request.
        machine: This node's hostname, stamped onto the event.

    Returns:
        The parsed :class:`Event` (``hook_event_name`` from the body, ``ts`` now,
        ``tool_name``/``tool_input`` populated for tool events, ``raw=body``).

    Raises:
        KeyError: If mandatory fields (``hook_event_name``, ``session_id``) are
            absent.
    """

    hook_event_name = body["hook_event_name"]
    session_id = body["session_id"]
    return Event(
        session_id=session_id,
        machine=machine,
        hook_event_name=hook_event_name,
        ts=utcnow(),
        cwd=body.get("cwd"),
        tool_name=body.get("tool_name"),
        tool_input=body.get("tool_input"),
        raw=body,
    )


@router.post("/hook")
async def receive_hook(body: dict[str, Any], request: Request) -> dict[str, str]:
    """Receive one hook event, journal it, broadcast the resulting snapshot.

    Parses the body via :func:`parse_hook_body`, appends it to the
    ``SessionStore`` on ``app.state.store``, and — if the daemon has installed a
    broadcaster on ``app.state.broadcast`` — triggers an SSE broadcast of the
    updated snapshot. Returns quickly with ``{"status": "ok"}``.
    """

    store = request.app.state.store
    event = parse_hook_body(body, machine=store.machine)
    snapshot = store.append(event)

    broadcast = getattr(request.app.state, "broadcast", None)
    if broadcast is not None:
        result = broadcast(snapshot)
        if inspect.isawaitable(result):
            await result

    return {"status": "ok"}
