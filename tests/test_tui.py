"""Tests for argus.tui — the departures-board Textual app."""

from __future__ import annotations

import json

from argus.config import ArgusConfig
from argus.models import FleetState, SessionSnapshot, SessionStatus
from argus.reply import ReplyOutcome, Result
from argus.tui import ArgusApp, _fleet_from_json


def test_bindings_cover_board_controls() -> None:
    # BINDINGS is class-level real data: arrows AND j/k nav, y/n reply, enter, quit.
    keys = {b[0] for b in ArgusApp.BINDINGS}
    assert {"down,j", "up,k", "y", "n", "enter", "q"} <= keys
    # Both arrow keys and vim keys are bound for navigation.
    assert any("down" in k and "j" in k for k in keys)
    assert any("up" in k and "k" in k for k in keys)


def test_app_constructs_from_config() -> None:
    app = ArgusApp(ArgusConfig())
    assert app is not None
    # SSE endpoint is derived from the daemon port; no connection is made yet.
    assert str(ArgusConfig().daemon_port) in app._sse_url
    assert app._sse_url.endswith("/api/state")


def test_fleet_from_json_parses_snapshots() -> None:
    payload = json.dumps(
        {
            "machines": {
                "astrape": [
                    {
                        "session_id": "s1",
                        "machine": "astrape",
                        "status": "blocked",
                        "question": "Run db migration? (y/n)",
                        "updated_at": "2026-07-16T00:00:00+00:00",
                    },
                    {"garbage": True},  # skipped: no session_id
                ]
            }
        }
    )
    fleet = _fleet_from_json(payload)
    sessions = fleet.all_sessions()
    assert len(sessions) == 1
    assert sessions[0].session_id == "s1"
    assert sessions[0].status is SessionStatus.BLOCKED
    assert sessions[0].question == "Run db migration? (y/n)"


def test_remote_attach_hint_names_machine_and_session() -> None:
    app = ArgusApp(ArgusConfig(machine="mac"), connect=False)
    session = SessionSnapshot(session_id="abc", machine="astrape")
    hint = app._remote_attach_hint(session)
    assert "ssh astrape" in hint
    assert "abc" in hint


def _fake_fleet() -> FleetState:
    fleet = FleetState()
    fleet.upsert(
        SessionSnapshot(
            session_id="hermes-fix",
            machine="astrape",
            status=SessionStatus.BLOCKED,
            question="Run db migration? (y/n)",
        )
    )
    fleet.upsert(
        SessionSnapshot(
            session_id="synthetes-ui",
            machine="mac",
            status=SessionStatus.TOOL,
            tool_name="Edit",
        )
    )
    fleet.upsert(
        SessionSnapshot(
            session_id="argus-docs",
            machine="mac",
            status=SessionStatus.DONE,
        )
    )
    return fleet


async def test_buckets_render_without_daemon() -> None:
    # Mount against injected state (connect=False => no live daemon needed) and
    # assert each session lands in its bucket region.
    app = ArgusApp(ArgusConfig(machine="mac"), connect=False)
    async with app.run_test() as pilot:
        app.apply_fleet(_fake_fleet())
        await pilot.pause()

        needs = str(app.query_one("#needs_you").render())
        working = str(app.query_one("#working").render())
        quiet = str(app.query_one("#quiet").render())

    assert "hermes-fix" in needs and "Run db migration? (y/n)" in needs
    assert "synthetes-ui" in working
    assert "argus-docs" in quiet
    # Blocked card is selected first (needs_you pins to the top of the list).
    selected = app._selected()
    assert selected is not None
    assert selected.session_id == "hermes-fix"


async def test_reply_yes_guarded_sends_against_blocked_card(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    def fake_guarded_send(session, text, expected_prompt, *, socket=None) -> Result:
        calls.append((text, expected_prompt))
        return Result(ReplyOutcome.SENT)

    monkeypatch.setattr("argus.tui.guarded_send", fake_guarded_send)

    app = ArgusApp(ArgusConfig(machine="mac"), connect=False)
    async with app.run_test() as pilot:
        app.apply_fleet(_fake_fleet())
        await pilot.pause()
        app.action_reply_yes()  # blocked card is selected by default
        await pilot.pause()

    assert calls == [("y", "Run db migration? (y/n)")]


async def test_reply_ignored_when_selection_not_blocked(monkeypatch) -> None:
    called = False

    def fake_guarded_send(*args, **kwargs) -> Result:
        nonlocal called
        called = True
        return Result(ReplyOutcome.SENT)

    monkeypatch.setattr("argus.tui.guarded_send", fake_guarded_send)

    fleet = FleetState()
    fleet.upsert(
        SessionSnapshot(
            session_id="only-working", machine="mac", status=SessionStatus.THINKING
        )
    )

    app = ArgusApp(ArgusConfig(machine="mac"), connect=False)
    async with app.run_test() as pilot:
        app.apply_fleet(fleet)
        await pilot.pause()
        app.action_reply_no()  # selection is a working session, not blocked
        await pilot.pause()

    assert called is False
