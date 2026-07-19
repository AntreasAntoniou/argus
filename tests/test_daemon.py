"""Behavior tests for argus.daemon — the FastAPI app factory + SSE wiring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from argus.config import ArgusConfig, Paths, Thresholds
from argus.correlate import PaneSession
from argus.daemon import (
    create_app,
    detect_blocked,
    mark_dead_sessions,
    visible_fleet,
)
from argus.models import FleetState, HookEvent, SessionSnapshot, SessionStatus, utcnow
from argus.store import SessionStore


def _isolated_config(tmp_path: Path) -> ArgusConfig:
    """A config whose journal + transcript roots are throwaway temp paths.

    Keeps the daemon off the real ``~/.argus`` and ``~/.claude`` during tests so
    the lifespan watcher finds nothing to backfill.
    """

    return ArgusConfig(
        machine="testbox",
        paths=Paths(
            claude_projects_root=tmp_path / "projects",  # absent → watcher no-ops
            journal_path=tmp_path / "journal.sqlite3",
            settings_path=tmp_path / "settings.json",
        ),
    )


def test_create_app_returns_fastapi_with_hook_route() -> None:
    app = create_app(ArgusConfig())
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/hook" in paths
    assert "/api/state" in paths


def test_hook_post_updates_readable_state(tmp_path: Path) -> None:
    app = create_app(_isolated_config(tmp_path))
    with TestClient(app) as client:  # enter lifespan (startup tasks run)
        resp = client.post(
            "/hook",
            json={
                "hook_event_name": HookEvent.NOTIFICATION.value,
                "session_id": "s-block",
                "cwd": "/work/x",
                "message": "Run db migration? (y/n)",
            },
        )
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        state = client.get("/api/state/snapshot").json()
        sessions = state["machines"]["testbox"]
        assert len(sessions) == 1
        assert sessions[0]["session_id"] == "s-block"
        assert sessions[0]["status"] == SessionStatus.BLOCKED.value


def test_token_gate_blocks_unauthenticated_and_allows_with_token(
    tmp_path: Path,
) -> None:
    config = _isolated_config(tmp_path)
    config.federation_token = "s3cret"
    app = create_app(config)
    with TestClient(app) as client:
        # No token → protected endpoints rejected.
        assert client.get("/api/state/snapshot").status_code == 401
        assert (
            client.post("/peer/state", json={"machines": {}}).status_code == 401
        )
        # Correct token → allowed.
        ok = client.get(
            "/api/state/snapshot", headers={"X-Argus-Token": "s3cret"}
        )
        assert ok.status_code == 200
        # Wrong token → rejected.
        assert (
            client.get(
                "/api/state/snapshot", headers={"X-Argus-Token": "nope"}
            ).status_code
            == 401
        )


def test_board_served_and_snapshot_token_via_query(tmp_path: Path) -> None:
    config = _isolated_config(tmp_path)
    config.federation_token = "s3cret"
    app = create_app(config)
    with TestClient(app) as client:
        # The board shell is public (no token) and is real HTML.
        page = client.get("/")
        assert page.status_code == 200
        assert "ARGUS" in page.text
        # The page is served no-store (it may carry the token for loopback).
        assert page.headers.get("cache-control") == "no-store"
        # Non-loopback caller (TestClient host is "testclient") must NOT receive
        # the baked-in token — it stays a placeholder, so the secret never leaks
        # to a remote viewer.
        assert "s3cret" not in page.text
        # The data endpoint takes the token via header only (never a URL param,
        # which would leak into access logs / browser history).
        assert (
            client.get(
                "/api/state/snapshot", headers={"X-Argus-Token": "s3cret"}
            ).status_code
            == 200
        )
        assert client.get("/api/state/snapshot?token=s3cret").status_code == 401


def test_no_token_leaves_endpoints_open(tmp_path: Path) -> None:
    # Default (empty token) preserves the local-only, no-auth behaviour.
    app = create_app(_isolated_config(tmp_path))
    with TestClient(app) as client:
        assert client.get("/api/state/snapshot").status_code == 200


def test_peer_state_merges_remote_machine(tmp_path: Path) -> None:
    app = create_app(_isolated_config(tmp_path))
    remote = {
        "machines": {
            "astrape": [
                {
                    "session_id": "r1",
                    "machine": "astrape",
                    "status": "thinking",
                    "updated_at": utcnow().isoformat(),
                }
            ]
        }
    }
    with TestClient(app) as client:
        client.post("/peer/state", json=remote)
        state = client.get("/api/state/snapshot").json()
        assert "astrape" in state["machines"]
        assert state["machines"]["astrape"][0]["session_id"] == "r1"


def test_visible_fleet_hides_stale_but_keeps_recent_and_blocked() -> None:
    now = utcnow()
    fleet = FleetState()
    fleet.upsert(
        SessionSnapshot("recent", "m", SessionStatus.THINKING, updated_at=now)
    )
    fleet.upsert(
        SessionSnapshot(
            "stale",
            "m",
            SessionStatus.DEAD,
            updated_at=now - timedelta(seconds=4000),
        )
    )
    fleet.upsert(
        SessionSnapshot(
            "old-block",
            "m",
            SessionStatus.BLOCKED,
            updated_at=now - timedelta(seconds=99999),  # waited for hours
        )
    )
    out = visible_fleet(fleet, now=now, window_seconds=1800)
    ids = {s.session_id for s in out.all_sessions()}
    assert ids == {"recent", "old-block"}  # stale dropped, blocked always kept


def test_visible_fleet_zero_window_shows_all() -> None:
    now = utcnow()
    fleet = FleetState()
    fleet.upsert(
        SessionSnapshot(
            "stale", "m", SessionStatus.DEAD, updated_at=now - timedelta(days=9)
        )
    )
    out = visible_fleet(fleet, now=now, window_seconds=0)
    assert {s.session_id for s in out.all_sessions()} == {"stale"}


def test_mark_dead_sessions_kills_silent_paneless_session(tmp_path: Path) -> None:
    # Death = silent past the window with no matched pane. A recently-active
    # session with no pane must survive (pane matching is unreliable).
    store = SessionStore(tmp_path / "j.sqlite3", "testbox")
    now = utcnow()
    store._snapshots["gone"] = SessionSnapshot(
        session_id="gone",
        machine="testbox",
        status=SessionStatus.THINKING,
        updated_at=now - timedelta(seconds=1200),  # past dead_after_seconds
    )
    store._snapshots["live"] = SessionSnapshot(
        session_id="live",
        machine="testbox",
        status=SessionStatus.THINKING,
        updated_at=now,  # fresh, no pane, must stay alive
    )
    changed = mark_dead_sessions(
        store,
        now=now,
        thresholds=Thresholds(dead_after_seconds=600),
        alive_session_ids=set(),  # nothing maps to a pane
    )
    assert [s.session_id for s in changed] == ["gone"]
    assert store.get("gone").status is SessionStatus.DEAD
    assert store.get("live").status is SessionStatus.THINKING


def test_detect_blocked_raises_blocked_from_pane_prompt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", "testbox")
    now = utcnow()
    store._snapshots["s1"] = SessionSnapshot(
        session_id="s1", machine="testbox", status=SessionStatus.THINKING
    )
    mapping = {"s1": PaneSession(pane_id="%3", session_id="s1", cwd="/w")}

    def capture(pane_id: str) -> str:
        assert pane_id == "%3"
        return "some output\nDo you want to proceed? (y/n)"

    changed = detect_blocked(
        store, mapping, machine="testbox", capture=capture, now=now
    )
    assert [s.session_id for s in changed] == ["s1"]
    snap = store.get("s1")
    assert snap.status is SessionStatus.BLOCKED
    assert "proceed" in snap.question.lower()


def test_detect_blocked_is_idempotent_on_same_prompt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", "testbox")
    now = utcnow()
    mapping = {"s1": PaneSession(pane_id="%3", session_id="s1", cwd="/w")}
    capture = lambda _p: "Do you want to proceed? (y/n)"  # noqa: E731

    first = detect_blocked(store, mapping, machine="testbox", capture=capture, now=now)
    assert len(first) == 1
    # Same prompt still on screen next sweep → no duplicate transition/event.
    second = detect_blocked(store, mapping, machine="testbox", capture=capture, now=now)
    assert second == []


def test_detect_blocked_ignores_pane_without_prompt(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", "testbox")
    store._snapshots["s1"] = SessionSnapshot(
        session_id="s1", machine="testbox", status=SessionStatus.TOOL
    )
    mapping = {"s1": PaneSession(pane_id="%3", session_id="s1", cwd="/w")}
    changed = detect_blocked(
        store,
        mapping,
        machine="testbox",
        capture=lambda _p: "working on it, running tests...",
        now=utcnow(),
    )
    assert changed == []
    assert store.get("s1").status is SessionStatus.TOOL


def test_mark_dead_sessions_keeps_live_pane_alive(tmp_path: Path) -> None:
    # A matched live pane keeps a long-silent session alive.
    store = SessionStore(tmp_path / "j.sqlite3", "testbox")
    now = utcnow()
    store._snapshots["panebound"] = SessionSnapshot(
        session_id="panebound",
        machine="testbox",
        status=SessionStatus.THINKING,
        updated_at=now - timedelta(seconds=1200),
    )
    changed = mark_dead_sessions(
        store,
        now=now,
        thresholds=Thresholds(dead_after_seconds=600),
        alive_session_ids={"panebound"},
    )
    assert changed == []
    assert store.get("panebound").status is SessionStatus.THINKING
