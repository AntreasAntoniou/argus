"""argus TUI — the departures-board Textual app.

STUB — precise typed contract only. Implementers: build the Textual app from
``DESIGN.md`` §Components and the README board sketch:

- Departures-board layout: NEEDS YOU queue pinned top, then working, then
  quiet/done/dead — driven by :meth:`argus.models.FleetState.bucketed`.
- ``j``/``k`` navigation between cards.
- ``y``/``n`` and typed replies -> :func:`argus.reply.guarded_send`.
- ``Enter`` attaches to the real pane (``tmux switch-client`` locally; an
  ssh+attach hint for a remote node).
- Drill-down: per-agent semantic timeline
  (:func:`argus.timeline.build_timeline`) + read-only diff pane
  (:class:`argus.diffs.DiffCache`).

State arrives over the daemon's SSE ``/api/state`` stream (``httpx`` client).
"""

from __future__ import annotations

from textual.app import App, ComposeResult

from argus.config import ArgusConfig


class ArgusApp(App[None]):
    """Textual application rendering the live fleet board."""

    BINDINGS = [
        ("j", "cursor_down", "Down"),
        ("k", "cursor_up", "Up"),
        ("y", "reply_yes", "Yes"),
        ("n", "reply_no", "No"),
        ("enter", "attach", "Attach"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: ArgusConfig) -> None:
        """Store config and SSE endpoint; do not connect yet.

        Args:
            config: Loaded config; ``daemon_port`` locates the local SSE stream.
        """

        raise NotImplementedError("super().__init__(), store config + SSE URL")

    def compose(self) -> ComposeResult:
        """Build the widget tree: needs-you queue, working list, quiet list, panes.

        Yields:
            The header, three bucket regions, and the timeline+diff drill-down.
        """

        raise NotImplementedError("Compose departures-board widget tree")

    async def on_mount(self) -> None:
        """Connect to the SSE state stream and start applying updates."""

        raise NotImplementedError("Open httpx SSE stream to /api/state, wire updates")

    def action_reply_yes(self) -> None:
        """Answer the selected blocked agent ``y`` via guarded injection."""

        raise NotImplementedError("guarded_send('y', selected.question)")

    def action_reply_no(self) -> None:
        """Answer the selected blocked agent ``n`` via guarded injection."""

        raise NotImplementedError("guarded_send('n', selected.question)")

    def action_attach(self) -> None:
        """Attach to the selected session's real pane (local switch / remote hint)."""

        raise NotImplementedError("tmux switch-client locally, ssh+attach hint remote")
