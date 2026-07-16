"""Behavior tests for argus.tui — the departures-board Textual app."""

from __future__ import annotations

from datetime import timedelta

import pytest

from argus.config import ArgusConfig
from argus.models import FleetState, SessionSnapshot, SessionStatus, utcnow
from argus.tui import ArgusApp


def test_bindings_cover_board_controls() -> None:
    # BINDINGS is class-level real data (j/k nav, y/n reply, enter attach, quit).
    keys = {b[0] for b in ArgusApp.BINDINGS}
    assert {"j", "k", "y", "n", "enter", "q"} <= keys


def test_app_constructs_from_config() -> None:
    app = ArgusApp(ArgusConfig())
    assert app is not None


def test_state_url_derived_from_daemon_port() -> None:
    app = ArgusApp(ArgusConfig(daemon_port=9999))
    assert app.state_url.endswith(":9999/api/state")
    assert app.state_url.startswith("http://")


def _fake_fleet() -> FleetState:
    now = utcnow()
    fleet = FleetState()
    fleet.upsert(
        SessionSnapshot(
            session_id="hermes-fix",
            machine="astrape",
            status=SessionStatus.BLOCKED,
            question="Run db migration? (y/n)",
            cwd=None,
            updated_at=now - timedelta(minutes=3),
        )
    )
    fleet.upsert(
        SessionSnapshot(
            session_id="synthetes-ui",
            machine="mac",
            status=SessionStatus.TOOL,
            tool_name="Edit",
            cwd=None,
            updated_at=now,
        )
    )
    fleet.upsert(
        SessionSnapshot(
            session_id="argus-docs",
            machine="mac",
            status=SessionStatus.DONE,
            cwd=None,
            updated_at=now - timedelta(minutes=12),
        )
    )
    return fleet


def _text(widget) -> str:
    return str(widget.render())


def _card_texts(app: ArgusApp, region: str) -> list[str]:
    node = app.query_one(region)
    return [_text(card) for card in node.query(".card")]


@pytest.mark.asyncio
async def test_three_buckets_render_without_daemon() -> None:
    # Mount the board against a fake FleetState — no live daemon, no network.
    app = ArgusApp(ArgusConfig())
    app.auto_connect = False  # never open the SSE stream in this test
    async with app.run_test() as pilot:
        app.apply_fleet(_fake_fleet())
        await pilot.pause()

        needs = _card_texts(app, "#needs-you")
        working = _card_texts(app, "#working")
        quiet = _card_texts(app, "#quiet")

        assert any("db migration" in t for t in needs)
        assert any("astrape" in t for t in needs)
        assert any("synthetes-ui" in t for t in working)
        assert any("argus-docs" in t for t in quiet)


@pytest.mark.asyncio
async def test_selected_defaults_to_first_needs_you() -> None:
    app = ArgusApp(ArgusConfig())
    app.auto_connect = False
    async with app.run_test() as pilot:
        app.apply_fleet(_fake_fleet())
        await pilot.pause()
        selected = app.selected
        assert selected is not None
        assert selected.session_id == "hermes-fix"
        assert selected.needs_you


@pytest.mark.asyncio
async def test_reply_yes_is_graceful_without_a_pane() -> None:
    # No matching tmux pane exists for the fake session; guarded_send must
    # refuse (NO_PANE / ERROR) and the action must not raise or crash the app.
    app = ArgusApp(ArgusConfig(machine="astrape"))
    app.auto_connect = False
    async with app.run_test() as pilot:
        app.apply_fleet(_fake_fleet())
        await pilot.pause()
        await pilot.press("y")  # dispatches action_reply_yes on the App
        await pilot.pause()
        # App survived; the status line records the (non-SENT) outcome.
        status = _text(app.query_one("#status-line"))
        assert status != ""
