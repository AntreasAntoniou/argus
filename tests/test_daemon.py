"""Behavior tests for argus.daemon — the FastAPI app factory + SSE wiring."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from argus.config import ArgusConfig, Paths, Thresholds
from argus.daemon import create_app, mark_dead_sessions
from argus.models import HookEvent, SessionSnapshot, SessionStatus, utcnow
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
