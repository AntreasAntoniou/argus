"""argus TUI — the departures-board Textual app.

Builds the Textual board from ``DESIGN.md`` §Components and the README sketch:

- Departures-board layout: NEEDS YOU queue pinned top, then working, then
  quiet/done/dead — driven by :meth:`argus.models.FleetState.bucketed`.
- ``j``/``k`` navigation between cards.
- ``y``/``n`` replies -> :func:`argus.reply.guarded_send` (guarded injection).
- ``Enter`` attaches to the real pane (``tmux switch-client`` locally; an
  ssh+attach hint for a remote node).
- Drill-down: per-agent semantic timeline
  (:func:`argus.timeline.build_timeline`) + read-only diff pane
  (:class:`argus.diffs.DiffCache`).

State arrives over the daemon's SSE ``/api/state`` stream (``httpx`` client).
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.widgets import Footer, Header, Static

from argus.config import ArgusConfig
from argus.correlate import correlate
from argus.diffs import DiffCache
from argus.ingest import tmux
from argus.models import FleetState, SessionSnapshot, SessionStatus, utcnow
from argus.reply import ReplyOutcome, Result, guarded_send
from argus.timeline import build_timeline


def _parse_dt(value: Any) -> datetime:
    """Parse an ISO-8601 wire timestamp into a tz-aware UTC datetime."""

    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return utcnow()
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return utcnow()


def _snapshot_from_json(data: dict[str, Any], machine: str) -> SessionSnapshot:
    """Reconstruct a :class:`SessionSnapshot` from one wire dict."""

    return SessionSnapshot(
        session_id=str(data["session_id"]),
        machine=str(data.get("machine", machine)),
        status=SessionStatus(data.get("status", SessionStatus.STARTING.value)),
        question=data.get("question"),
        cwd=data.get("cwd"),
        branch=data.get("branch"),
        tokens=int(data.get("tokens", 0) or 0),
        last_tool=data.get("last_tool"),
        updated_at=_parse_dt(data.get("updated_at")),
        tool_name=data.get("tool_name"),
    )


def _fleet_from_json(text: str) -> FleetState:
    """Decode an SSE ``data:`` payload into a :class:`FleetState`.

    The daemon serialises ``FleetState`` as ``{"machines": {host: [snapshot,
    ...]}}``; unknown keys are ignored and malformed snapshots are skipped so a
    single bad row never blanks the board.
    """

    data = json.loads(text)
    fleet = FleetState()
    machines = data.get("machines", {}) if isinstance(data, dict) else {}
    for machine, snapshots in machines.items():
        for snapshot in snapshots or []:
            if isinstance(snapshot, dict) and snapshot.get("session_id"):
                fleet.upsert(_snapshot_from_json(snapshot, machine))
    return fleet


class ArgusApp(App[None]):
    """Textual application rendering the live fleet board."""

    TITLE = "ARGUS"

    CSS = """
    #body { height: 1fr; }
    #board { width: 2fr; }
    #drilldown { width: 1fr; border-left: solid $panel; }
    #needs_you { color: $warning; }
    #timeline { height: 2fr; }
    #diff { height: 1fr; border-top: solid $panel; }
    Static { padding: 0 1; }
    """

    BINDINGS = [
        ("down,j", "cursor_down", "Down"),
        ("up,k", "cursor_up", "Up"),
        ("y", "reply_yes", "Yes"),
        ("n", "reply_no", "No"),
        ("enter", "attach", "Attach"),
        ("q", "quit", "Quit"),
    ]

    def __init__(self, config: ArgusConfig, *, connect: bool = True) -> None:
        """Store config and SSE endpoint; do not connect yet.

        Args:
            config: Loaded config; ``daemon_port`` locates the local SSE stream.
            connect: When ``False`` the SSE worker is not started on mount
                (headless construction / tests render from injected state).
        """

        super().__init__()
        self._config = config
        self._sse_url = f"http://127.0.0.1:{config.daemon_port}/api/state"
        self._connect = connect
        self._fleet = FleetState()
        self._cards: list[SessionSnapshot] = []
        self._cursor = 0
        self._diffs = DiffCache()

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        """Build the widget tree: needs-you queue, working list, quiet list, panes.

        Yields:
            The header, three bucket regions, the timeline+diff drill-down, and
            the footer key legend.
        """

        yield Header()
        with Horizontal(id="body"):
            with VerticalScroll(id="board"):
                yield Static(id="needs_you")
                yield Static(id="working")
                yield Static(id="quiet")
            with Vertical(id="drilldown"):
                yield Static(id="timeline")
                yield Static(id="diff")
        yield Footer()

    async def on_mount(self) -> None:
        """Render the initial board and open the SSE state stream."""

        self._render()
        if self._connect:
            self.run_worker(self._stream_state(), name="sse", exclusive=True)

    # -- state ingestion --------------------------------------------------

    async def _stream_state(self) -> None:
        """Stream :class:`FleetState` snapshots from the daemon over SSE.

        Reconnects with a short backoff so the board recovers when the daemon
        restarts; network failure is contained here and never crashes the app.
        """

        import httpx

        headers = (
            {"X-Argus-Token": self._config.federation_token}
            if self._config.federation_token
            else None
        )
        while True:
            try:
                timeout = httpx.Timeout(None, connect=5.0)
                async with httpx.AsyncClient(
                    timeout=timeout, headers=headers
                ) as client:
                    async with client.stream("GET", self._sse_url) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            payload = line[len("data:") :].strip()
                            if not payload:
                                continue
                            try:
                                fleet = _fleet_from_json(payload)
                            except (ValueError, KeyError):
                                continue
                            self.apply_fleet(fleet)
            except (httpx.HTTPError, OSError):
                await asyncio.sleep(2.0)
            else:
                await asyncio.sleep(1.0)

    def apply_fleet(self, fleet: FleetState) -> None:
        """Adopt a new fleet snapshot and re-render the board."""

        self._fleet = fleet
        self._rebuild_cards()
        self._render()

    def _rebuild_cards(self) -> None:
        """Flatten the bucketed board into the navigable card list."""

        buckets = self._fleet.bucketed()
        self._cards = [*buckets.needs_you, *buckets.working, *buckets.quiet]
        if self._cursor >= len(self._cards):
            self._cursor = max(0, len(self._cards) - 1)

    def _selected(self) -> SessionSnapshot | None:
        """The session under the cursor, or ``None`` when the board is empty."""

        if 0 <= self._cursor < len(self._cards):
            return self._cards[self._cursor]
        return None

    # -- rendering --------------------------------------------------------

    def _render(self) -> None:
        """Repaint the three bucket regions and the drill-down panes."""

        try:
            needs = self.query_one("#needs_you", Static)
            working = self.query_one("#working", Static)
            quiet = self.query_one("#quiet", Static)
        except NoMatches:
            return  # not mounted yet

        buckets = self._fleet.bucketed()
        selected = self._selected()
        selected_id = selected.session_id if selected else None
        needs.update(
            self._bucket_text(
                "⚠ NEEDS YOU", buckets.needs_you, selected_id, blocked=True
            )
        )
        working.update(self._bucket_text("── working", buckets.working, selected_id))
        # The quiet bucket (idle/done/dead) is context, not signal — collapse it
        # so a day's worth of finished sessions can never bury the live board.
        quiet.update(
            self._bucket_text("── quiet", buckets.quiet, selected_id, limit=6)
        )
        self.sub_title = f"{len(self._fleet.all_sessions())} eyes open"
        self._update_drilldown(selected)

    def _bucket_text(
        self,
        title: str,
        sessions: list[SessionSnapshot],
        selected_id: str | None,
        *,
        blocked: bool = False,
        limit: int | None = None,
    ) -> str:
        """Render one bucket as a titled block of card rows.

        Rows are ``marker id  cwd-basename  label`` — the working directory's
        leaf is far more identifying at a glance than a bare session UUID. When
        ``limit`` is set, only the first ``limit`` rows are shown followed by a
        "… and N more" line, so low-signal buckets stay compact.
        """

        lines = [f"{title} ({len(sessions)})"]
        if not sessions:
            lines.append("  —")
        shown = sessions if limit is None else sessions[:limit]
        for session in shown:
            marker = "▸ " if session.session_id == selected_id else "  "
            where = Path(session.cwd).name if session.cwd else session.machine
            row = f"{marker}{session.session_id[:12]:12}  {where}  {session.label()}"
            if blocked and session.question:
                row += f'  "{session.question}"'
            lines.append(row)
        hidden = len(sessions) - len(shown)
        if hidden > 0:
            lines.append(f"  … and {hidden} more")
        return "\n".join(lines)

    def _update_drilldown(self, session: SessionSnapshot | None) -> None:
        """Repaint the timeline + diff panes for the selected session."""

        try:
            timeline = self.query_one("#timeline", Static)
            diff = self.query_one("#diff", Static)
        except NoMatches:
            return

        if session is None:
            timeline.update("no session selected")
            diff.update("")
            return
        timeline.update(self._timeline_text(session))
        diff.update(self._diff_text(session))

    def _timeline_text(self, session: SessionSnapshot) -> str:
        """Render the tail of the semantic timeline for ``session``."""

        rows = build_timeline([], transcript_path=self._transcript_for(session))
        if not rows:
            return f"{session.session_id}\n(no timeline yet)"
        tail = rows[-12:]
        return "\n".join(f"{row.kind}: {row.summary}" for row in tail)

    def _diff_text(self, session: SessionSnapshot) -> str:
        """Render the read-only working-tree diff for a local session's cwd."""

        if not session.cwd or session.machine != self._config.machine:
            return f"branch: {session.branch or '—'} (remote)"
        stat = self._diffs.get(Path(session.cwd))
        header = (
            f"branch: {stat.branch or session.branch or '—'}  "
            f"files: {stat.files_changed}  +{stat.added_lines}/-{stat.removed_lines}"
        )
        return f"{header}\n\n{stat.detail}" if stat.detail else header

    def _transcript_for(self, session: SessionSnapshot) -> Path | None:
        """Locate a local session's JSONL transcript for timeline drill-down."""

        if session.machine != self._config.machine:
            return None
        root = self._config.paths.claude_projects_root
        try:
            return next(root.rglob(f"{session.session_id}.jsonl"), None)
        except OSError:
            return None

    # -- actions ----------------------------------------------------------

    def action_cursor_down(self) -> None:
        """Move the selection cursor down one card."""

        if self._cards:
            self._cursor = min(self._cursor + 1, len(self._cards) - 1)
            self._render()

    def action_cursor_up(self) -> None:
        """Move the selection cursor up one card."""

        if self._cards:
            self._cursor = max(self._cursor - 1, 0)
            self._render()

    def action_reply_yes(self) -> None:
        """Answer the selected blocked agent ``y`` via guarded injection."""

        self._reply("y")

    def action_reply_no(self) -> None:
        """Answer the selected blocked agent ``n`` via guarded injection."""

        self._reply("n")

    def _reply(self, text: str) -> None:
        """Send ``text`` to the selected blocked session, guarding the prompt."""

        session = self._selected()
        if session is None or not session.needs_you:
            self.notify("no blocked session selected", severity="warning")
            return
        result = guarded_send(session, text, session.question or "")
        self._announce_reply(session, result)

    def _announce_reply(self, session: SessionSnapshot, result: Result) -> None:
        """Surface a guarded-send outcome to the operator."""

        if result.outcome is ReplyOutcome.SENT:
            self.notify(f"sent to {session.session_id}")
        elif result.outcome is ReplyOutcome.STALE:
            self.notify("prompt changed — refresh the card", severity="warning")
        elif result.outcome is ReplyOutcome.NO_PANE:
            self.notify("no live pane for this session", severity="warning")
        else:
            self.notify(result.detail or "reply failed", severity="error")

    def action_attach(self) -> None:
        """Attach to the selected session's real pane (local switch / remote hint)."""

        session = self._selected()
        if session is None:
            self.notify("no session selected", severity="warning")
            return
        if session.machine == self._config.machine:
            self._attach_local(session)
        else:
            self.notify(self._remote_attach_hint(session))

    def _remote_attach_hint(self, session: SessionSnapshot) -> str:
        """The ssh command that attaches to a remote session's tmux."""

        return f"ssh {session.machine} -t tmux attach -t {session.session_id}"

    def _attach_local(self, session: SessionSnapshot) -> None:
        """Switch this tmux client to the session's live pane.

        Uses the same process-argv/cwd correlation the daemon uses — a Claude
        session id is a UUID that never equals a tmux pane name, so matching on
        ``session_name``/``title`` (the old approach) always missed.
        """

        try:
            panes = tmux.list_panes()
        except FileNotFoundError:
            self.notify("tmux not available", severity="error")
            return
        mapping = correlate(
            panes, projects_root=self._config.paths.claude_projects_root
        )
        pane = mapping.get(session.session_id)
        if pane is None:
            self.notify("no live tmux pane for this session", severity="warning")
            return
        try:
            tmux.focus_pane(pane.pane_id)
        except OSError as exc:
            self.notify(f"attach failed: {exc}", severity="error")
