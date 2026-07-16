"""argus TUI — the departures-board Textual app.

Builds the Textual app from ``DESIGN.md`` §Components and the README board
sketch:

- Departures-board layout: NEEDS YOU queue pinned top, then working, then
  quiet/done/dead — driven by :meth:`argus.models.FleetState.bucketed`.
- ``j``/``k`` navigation between cards.
- ``y``/``n`` replies -> :func:`argus.reply.guarded_send` (guarded injection).
- ``Enter`` attaches to the real pane (``tmux switch-client`` locally; an
  ssh+attach hint for a remote node).
- Drill-down: per-agent summary + read-only diff pane
  (:class:`argus.diffs.DiffCache`).

State arrives over the daemon's SSE ``/api/state`` stream (``httpx`` client).
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import httpx
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.widgets import Footer, Static

from argus.config import ArgusConfig
from argus.diffs import DiffCache
from argus.federation import Federation
from argus.ingest import tmux
from argus.models import FleetState, SessionSnapshot, SessionStatus, utcnow
from argus.reply import ReplyOutcome, guarded_send


class ArgusApp(App[None]):
    """Textual application rendering the live fleet board."""

    CSS = """
    #board-header { dock: top; height: 1; background: $panel; color: $accent; }
    #status-line { dock: bottom; height: 1; color: $text-muted; }
    #board { width: 2fr; }
    #drill { width: 1fr; border-left: solid $panel; }
    .section { color: $text-muted; text-style: bold; }
    .card { height: 1; }
    .card.selected { background: $accent; color: $text; }
    """

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

        super().__init__()
        self.config = config
        self.machine = config.machine
        # Local daemon SSE endpoint; the connection is opened in ``on_mount``.
        self.state_url = f"http://127.0.0.1:{config.daemon_port}/api/state"
        # Flipped off by headless tests so mounting never touches the network.
        self.auto_connect = True
        self.fleet = FleetState()
        self._diffs = DiffCache()
        # Board order: needs_you, then working, then quiet (see ``bucketed``).
        self._cards: list[SessionSnapshot] = []
        self._cursor = 0

    def compose(self) -> ComposeResult:
        """Build the widget tree: needs-you queue, working list, quiet list, panes.

        Yields:
            The header, three bucket regions, and the summary+diff drill-down.
        """

        yield Static("ARGUS", id="board-header")
        with Horizontal():
            with Vertical(id="board"):
                yield Static("⚠ NEEDS YOU", classes="section")
                yield VerticalScroll(id="needs-you")
                yield Static("── working", classes="section")
                yield VerticalScroll(id="working")
                yield Static("── quiet", classes="section")
                yield VerticalScroll(id="quiet")
            with Vertical(id="drill"):
                yield Static("", id="timeline")
                yield Static("", id="diff")
        yield Static("", id="status-line")
        yield Footer()

    async def on_mount(self) -> None:
        """Connect to the SSE state stream and start applying updates."""

        self._render_board()
        if self.auto_connect:
            # Background worker; a down/absent daemon must never block mount.
            self.run_worker(self._consume_state(), name="sse-state", exclusive=True)

    # -- state ingestion -------------------------------------------------------

    async def _consume_state(self) -> None:
        """Stream :class:`FleetState` snapshots from the daemon SSE endpoint.

        Network failures are swallowed: a missing daemon leaves the board empty
        rather than crashing the TUI (guards headless/offline construction).
        """

        try:
            timeout = httpx.Timeout(5.0, read=None)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("GET", self.state_url) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        fleet = _parse_sse_line(line)
                        if fleet is not None:
                            self.apply_fleet(fleet)
        except httpx.HTTPError, OSError:
            return

    def apply_fleet(self, fleet: FleetState) -> None:
        """Replace the rendered fleet and refresh the board."""

        self.fleet = fleet
        self._render_board()

    # -- rendering -------------------------------------------------------------

    def _render_board(self) -> None:
        """Repopulate the three bucket regions from :meth:`FleetState.bucketed`."""

        buckets = self.fleet.bucketed()
        self._cards = [*buckets.needs_you, *buckets.working, *buckets.quiet]
        if self._cursor >= len(self._cards):
            self._cursor = max(0, len(self._cards) - 1)

        self._fill_region("#needs-you", buckets.needs_you, _needs_you_line)
        self._fill_region("#working", buckets.working, _working_line)
        self._fill_region("#quiet", buckets.quiet, _quiet_line)

        open_eyes = len(self.fleet.all_sessions())
        self.query_one("#board-header", Static).update(f"ARGUS — {open_eyes} eyes open")
        self._render_drill()

    def _fill_region(
        self,
        selector: str,
        snapshots: list[SessionSnapshot],
        renderer: Callable[[SessionSnapshot], str],
    ) -> None:
        region = self.query_one(selector, VerticalScroll)
        region.remove_children()
        selected = self.selected
        for snap in snapshots:
            card = Static(renderer(snap), classes="card")
            if snap is selected:
                card.add_class("selected")
            region.mount(card)

    def _render_drill(self) -> None:
        """Refresh the drill-down: session summary + read-only diff of its cwd."""

        snap = self.selected
        timeline = self.query_one("#timeline", Static)
        diff = self.query_one("#diff", Static)
        if snap is None:
            timeline.update("")
            diff.update("")
            return

        timeline.update(
            f"{snap.session_id} @ {snap.machine}\n"
            f"state   {snap.label()}\n"
            f"branch  {snap.branch or '-'}\n"
            f"tokens  {snap.tokens}\n"
            f"tool    {snap.last_tool or '-'}"
        )
        diff.update(self._diff_detail(snap))

    def _diff_detail(self, snap: SessionSnapshot) -> str:
        """Read-only ``git diff --stat`` of the session's cwd (never raises)."""

        if not snap.cwd:
            return "(no cwd)"
        cwd = Path(snap.cwd)
        if not cwd.is_dir():
            return "(cwd gone)"
        stat = self._diffs.get(cwd)
        head = (
            f"{stat.branch or '-'}  "
            f"{stat.files_changed} files  +{stat.added_lines}/-{stat.removed_lines}"
        )
        return f"{head}\n{stat.detail}".rstrip() if stat.detail else head

    # -- selection / navigation ------------------------------------------------

    @property
    def selected(self) -> SessionSnapshot | None:
        """The snapshot under the board cursor, or ``None`` when empty."""

        if 0 <= self._cursor < len(self._cards):
            return self._cards[self._cursor]
        return None

    def action_cursor_down(self) -> None:
        """Move the board cursor down one card."""

        self._move_cursor(1)

    def action_cursor_up(self) -> None:
        """Move the board cursor up one card."""

        self._move_cursor(-1)

    def _move_cursor(self, delta: int) -> None:
        if not self._cards:
            return
        self._cursor = max(0, min(len(self._cards) - 1, self._cursor + delta))
        self._render_board()

    # -- reply / attach --------------------------------------------------------

    def action_reply_yes(self) -> None:
        """Answer the selected blocked agent ``y`` via guarded injection."""

        self._reply("y")

    def action_reply_no(self) -> None:
        """Answer the selected blocked agent ``n`` via guarded injection."""

        self._reply("n")

    def _reply(self, text: str) -> None:
        snap = self.selected
        if snap is None or not snap.needs_you:
            self._status("no blocked agent selected")
            return
        if snap.machine != self.machine:
            self._status(f"remote session on {snap.machine} — attach to reply")
            return
        result = guarded_send(snap, text, snap.question or "")
        if result.outcome is ReplyOutcome.SENT:
            self._status(f"sent {text!r} to {snap.session_id}")
        else:
            self._status(f"{result.outcome.value}: {result.detail}")

    def action_attach(self) -> None:
        """Attach to the selected session's real pane (local switch / remote hint)."""

        snap = self.selected
        if snap is None:
            self._status("nothing selected")
            return
        if snap.machine != self.machine:
            self._status(f"ssh {snap.machine} -t tmux attach -t {snap.session_id}")
            return
        try:
            subprocess.run(
                tmux._tmux("switch-client", "-t", snap.session_id, socket=None),
                check=True,
                capture_output=True,
            )
        except (FileNotFoundError, subprocess.CalledProcessError) as exc:
            self._status(f"attach failed: {exc}")
            return
        self._status(f"attached to {snap.session_id}")

    # -- helpers ---------------------------------------------------------------

    def _status(self, text: str) -> None:
        self.query_one("#status-line", Static).update(text)


def _parse_sse_line(line: str) -> FleetState | None:
    """Decode one ``data:`` SSE line into a :class:`FleetState`, or ``None``.

    Reuses :meth:`argus.federation.Federation.load_fleet` so the TUI and the
    federation wire format stay in lockstep.
    """

    if not line.startswith("data:"):
        return None
    payload = line[len("data:") :].strip()
    if not payload:
        return None
    try:
        return Federation.load_fleet(json.loads(payload))
    except json.JSONDecodeError, KeyError, ValueError:
        return None


def _age(snap: SessionSnapshot) -> str:
    """Compact human age since the snapshot was last touched (e.g. ``3m``)."""

    seconds = int((utcnow() - snap.updated_at).total_seconds())
    if seconds < 60:
        return f"{max(seconds, 0)}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h"


def _needs_you_line(snap: SessionSnapshot) -> str:
    return f"▸ {snap.session_id}  {snap.machine}  “{snap.question or ''}”"


def _working_line(snap: SessionSnapshot) -> str:
    return f"● {snap.session_id}  {snap.machine}  {snap.label()}  {_age(snap)}"


def _quiet_line(snap: SessionSnapshot) -> str:
    mark = "☠" if snap.status is SessionStatus.DEAD else "✓"
    return f"{mark} {snap.session_id}  {snap.machine}  {snap.label()}  {_age(snap)}"
