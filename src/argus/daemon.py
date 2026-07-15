"""argusd — the FastAPI daemon: ingesters, reducer, SSE, federation, notifier.

STUB — precise typed contract only. Implementers: :func:`create_app` is the app
factory that wires everything together (``DESIGN.md`` §Components). It mounts the
hook router, exposes an SSE ``/api/state`` stream of :class:`FleetState`
snapshots, and on startup launches the JSONL watcher, tmux poller, federation
exchange loop, and notifier batcher as background tasks bound to the app
lifespan.
"""

from __future__ import annotations

from fastapi import FastAPI

from argus.config import ArgusConfig


def create_app(config: ArgusConfig) -> FastAPI:
    """Build and wire the ``argusd`` FastAPI application.

    Wiring contract:
        - Construct a :class:`argus.store.SessionStore` (recover from journal)
          and stash it on ``app.state.store``.
        - ``app.include_router`` the :data:`argus.ingest.hooks.router`.
        - Register ``GET /api/state`` as an SSE endpoint (``sse-starlette``)
          streaming merged :class:`argus.models.FleetState` on every change.
        - Register peer endpoints (``POST /peer/event``, ``POST /peer/state``)
          for :class:`argus.federation.Federation`.
        - On the lifespan startup: start the transcript watcher
          (:func:`argus.ingest.transcripts.watch_transcripts`), the tmux poller,
          the federation loop (:meth:`argus.federation.Federation.run`), and the
          notifier batcher; cancel them all on shutdown.

    Args:
        config: Loaded :class:`ArgusConfig` for this node.

    Returns:
        The configured :class:`fastapi.FastAPI` app (not yet served).
    """

    raise NotImplementedError("Wire store + hooks router + SSE + federation + notifier")


async def state_stream(app: FastAPI):  # noqa: ANN201  (SSE generator, typed by impl)
    """Yield SSE events as the fleet state changes.

    Async-generates ``ServerSentEvent`` payloads (JSON-encoded
    :class:`argus.models.FleetState`) whenever a snapshot updates, so the TUI
    re-renders within the ``DESIGN.md`` v1-acceptance 2s budget.

    Args:
        app: The running application (for access to ``app.state``).

    Yields:
        SSE event objects.
    """

    raise NotImplementedError("Subscribe to state changes, yield SSE events")
