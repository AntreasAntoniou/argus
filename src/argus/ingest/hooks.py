"""Hook receiver — FastAPI router turning Claude Code hook POSTs into Events.

STUB — precise typed contract only. Implementers: mount :data:`router` on the
daemon app. It exposes ``POST /hook`` accepting the JSON body the async hook
pack sends for each of the eight lifecycle events
(:class:`argus.models.HookEvent`). Parse the body into an
:class:`argus.models.Event` and hand it to the store (dependency-injected).

The hook body carries at least ``hook_event_name``, ``session_id``, ``cwd``,
and — for tool hooks — ``tool_name`` and ``tool_input``. ``Notification`` bodies
carry the question/permission text. Respond ``2xx`` fast; the hooks are async
and must never block a session.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from argus.models import Event

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

    raise NotImplementedError("Map hook body fields onto an Event")


@router.post("/hook")
async def receive_hook(body: dict[str, Any]) -> dict[str, str]:
    """Receive one hook event, journal it, broadcast the resulting snapshot.

    Parses the body via :func:`parse_hook_body`, appends it to the
    ``SessionStore`` on ``app.state``, and triggers an SSE broadcast of the
    updated snapshot. Returns quickly with ``{"status": "ok"}``.
    """

    raise NotImplementedError("Parse body, store.append, broadcast; return ok")
