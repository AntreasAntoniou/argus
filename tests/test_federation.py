"""Intended-behavior tests for argus.federation (stub → pass once implemented)."""

from __future__ import annotations

from datetime import timedelta

import httpx

from argus.config import ArgusConfig
from argus.federation import Federation
from argus.models import (
    Event,
    FleetState,
    SessionSnapshot,
    SessionStatus,
    utcnow,
)


def _fleet(*snapshots: SessionSnapshot) -> FleetState:
    fleet = FleetState()
    for snap in snapshots:
        fleet.upsert(snap)
    return fleet


def _client(handler) -> httpx.AsyncClient:
    """An httpx client backed by a mock transport — no live network."""

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_exchange_merges_peer_machines() -> None:
    fed = Federation(ArgusConfig(machine="mac", peers=[]))
    local = _fleet(
        SessionSnapshot(session_id="m1", machine="mac", status=SessionStatus.THINKING)
    )
    merged = await fed.exchange(local)
    # With no peers reachable, the merged view is at least the local one.
    assert "mac" in merged.machines


async def test_merge_remote_adds_foreign_machine() -> None:
    fed = Federation(ArgusConfig(machine="mac", peers=[]))
    remote = _fleet(
        SessionSnapshot(
            session_id="a1",
            machine="astrape",
            status=SessionStatus.BLOCKED,
            question="q",
        )
    )
    await fed.merge_remote(remote)
    merged = await fed.exchange(FleetState())
    assert "astrape" in merged.machines
    astrape = merged.machines["astrape"]
    assert [s.session_id for s in astrape] == ["a1"]
    assert astrape[0].question == "q"


async def test_exchange_pulls_and_merges_peer_state() -> None:
    """A live peer's FleetState is folded into the returned merged view."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/peer/state"
        remote = _fleet(
            SessionSnapshot(
                session_id="f1",
                machine="forge",
                status=SessionStatus.TOOL,
                tool_name="Bash",
            )
        )
        return httpx.Response(200, json=Federation.dump_fleet(remote))

    fed = Federation(
        ArgusConfig(machine="mac", peers=["forge:8787"]), client=_client(handler)
    )
    local = _fleet(
        SessionSnapshot(session_id="m1", machine="mac", status=SessionStatus.THINKING)
    )
    merged = await fed.exchange(local)
    assert set(merged.machines) == {"mac", "forge"}
    forge = merged.machines["forge"][0]
    assert forge.status is SessionStatus.TOOL
    assert forge.label() == "tool:Bash"


async def test_lww_keeps_newer_snapshot_for_same_session() -> None:
    """When two sources report the same session_id, the newer updated_at wins."""

    now = utcnow()
    old = SessionSnapshot(
        session_id="s1",
        machine="mac",
        status=SessionStatus.THINKING,
        updated_at=now - timedelta(seconds=30),
    )
    new = SessionSnapshot(
        session_id="s1",
        machine="mac",
        status=SessionStatus.BLOCKED,
        question="need input",
        updated_at=now,
    )

    # A peer echoes back a STALE copy of mac's own session; local holds the fresh one.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=Federation.dump_fleet(_fleet(old)))

    fed = Federation(
        ArgusConfig(machine="mac", peers=["astrape:8787"]), client=_client(handler)
    )
    merged = await fed.exchange(_fleet(new))
    sessions = merged.machines["mac"]
    assert len(sessions) == 1
    assert sessions[0].status is SessionStatus.BLOCKED
    assert sessions[0].question == "need input"

    # And the reverse: a peer with the NEWER copy overrides a stale local view.
    def handler_newer(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=Federation.dump_fleet(_fleet(new)))

    fed2 = Federation(
        ArgusConfig(machine="mac", peers=["astrape:8787"]),
        client=_client(handler_newer),
    )
    merged2 = await fed2.exchange(_fleet(old))
    assert merged2.machines["mac"][0].status is SessionStatus.BLOCKED


async def test_push_event_survives_down_peer() -> None:
    """A dead peer must not make push_event raise."""

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    fed = Federation(
        ArgusConfig(machine="mac", peers=["dead:8787", "alsodead:8787"]),
        client=_client(down),
    )
    event = Event(session_id="m1", machine="mac", hook_event_name="PreToolUse")
    # Must complete without raising even though every peer is unreachable.
    await fed.push_event(event)


async def test_exchange_survives_down_peer() -> None:
    """A dead peer must not make exchange raise; local state still returned."""

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    fed = Federation(
        ArgusConfig(machine="mac", peers=["dead:8787"]), client=_client(down)
    )
    local = _fleet(
        SessionSnapshot(session_id="m1", machine="mac", status=SessionStatus.THINKING)
    )
    merged = await fed.exchange(local)
    assert "mac" in merged.machines


async def test_push_event_posts_to_every_peer() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/peer/event"
        seen.append(request.url.host)
        return httpx.Response(204)

    fed = Federation(
        ArgusConfig(machine="mac", peers=["forge:8787", "astrape:8787"]),
        client=_client(handler),
    )
    await fed.push_event(
        Event(session_id="m1", machine="mac", hook_event_name="Stop")
    )
    assert set(seen) == {"forge", "astrape"}


async def test_fleet_roundtrip_serialization() -> None:
    """dump_fleet / load_fleet preserve every snapshot field across the wire."""

    now = utcnow()
    snap = SessionSnapshot(
        session_id="s1",
        machine="odysseus",
        status=SessionStatus.TOOL,
        question=None,
        cwd="/work",
        branch="main",
        tokens=1234,
        last_tool="Read",
        updated_at=now,
        tool_name="Bash",
    )
    restored = Federation.load_fleet(Federation.dump_fleet(_fleet(snap)))
    got = restored.machines["odysseus"][0]
    assert got.session_id == "s1"
    assert got.status is SessionStatus.TOOL
    assert got.cwd == "/work"
    assert got.branch == "main"
    assert got.tokens == 1234
    assert got.last_tool == "Read"
    assert got.tool_name == "Bash"
    assert got.updated_at == now
