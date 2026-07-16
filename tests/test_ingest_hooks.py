"""Behaviour tests for argus.ingest.hooks (parse_hook_body + POST /hook)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from argus.ingest.hooks import parse_hook_body, router
from argus.models import Event, HookEvent, SessionSnapshot, SessionStatus


def test_parse_pretooluse_body_extracts_tool() -> None:
    body = {
        "hook_event_name": HookEvent.PRE_TOOL_USE.value,
        "session_id": "s1",
        "cwd": "/home/dev/example",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/home/dev/example/a.py"},
    }
    ev = parse_hook_body(body, machine="mac")
    assert isinstance(ev, Event)
    assert ev.session_id == "s1"
    assert ev.machine == "mac"
    assert ev.hook_event_name == HookEvent.PRE_TOOL_USE.value
    assert ev.cwd == "/home/dev/example"
    assert ev.tool_name == "Edit"
    assert ev.tool_input == {"file_path": "/home/dev/example/a.py"}
    assert ev.raw == body


def test_parse_missing_mandatory_field_raises() -> None:
    with pytest.raises(KeyError):
        parse_hook_body({"cwd": "/home/dev/example"}, machine="mac")


def test_parse_missing_session_id_raises() -> None:
    with pytest.raises(KeyError):
        parse_hook_body(
            {"hook_event_name": HookEvent.SESSION_START.value}, machine="mac"
        )


def test_parse_notification_body_is_blocked_bound_with_question() -> None:
    body = {
        "hook_event_name": HookEvent.NOTIFICATION.value,
        "session_id": "s2",
        "cwd": "/home/dev/example",
        "message": "Run db migration? (y/n)",
    }
    ev = parse_hook_body(body, machine="mac")
    assert ev.hook_event_name == HookEvent.NOTIFICATION.value
    assert ev.tool_name is None
    assert ev.tool_input is None
    # The pending question rides in raw for the reducer to lift into BLOCKED.
    assert ev.raw is not None
    assert ev.raw["message"] == "Run db migration? (y/n)"


class _StubStore:
    """Minimal SessionStore stand-in: records appends, returns a snapshot."""

    def __init__(self, machine: str = "mac") -> None:
        self.machine = machine
        self.appended: list[Event] = []

    def append(self, event: Event) -> SessionSnapshot:
        self.appended.append(event)
        return SessionSnapshot(
            session_id=event.session_id,
            machine=event.machine,
            status=SessionStatus.TOOL,
            tool_name=event.tool_name,
        )


def _client(store: _StubStore, broadcast: Any = None) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.store = store
    if broadcast is not None:
        app.state.broadcast = broadcast
    return TestClient(app)


def test_receive_hook_appends_and_broadcasts() -> None:
    store = _StubStore(machine="mac")
    seen: list[SessionSnapshot] = []

    async def broadcast(snapshot: SessionSnapshot) -> None:
        seen.append(snapshot)

    client = _client(store, broadcast=broadcast)
    body = {
        "hook_event_name": HookEvent.PRE_TOOL_USE.value,
        "session_id": "s1",
        "cwd": "/x",
        "tool_name": "Edit",
        "tool_input": {},
    }
    resp = client.post("/hook", json=body)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(store.appended) == 1
    assert store.appended[0].tool_name == "Edit"
    assert store.appended[0].machine == "mac"
    assert [s.session_id for s in seen] == ["s1"]


def test_receive_hook_without_broadcast_still_ok() -> None:
    store = _StubStore()
    client = _client(store)
    body = {
        "hook_event_name": HookEvent.NOTIFICATION.value,
        "session_id": "s2",
        "message": "Approve? (y/n)",
    }
    resp = client.post("/hook", json=body)

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert len(store.appended) == 1
    assert store.appended[0].raw is not None
    assert store.appended[0].raw["message"] == "Approve? (y/n)"
