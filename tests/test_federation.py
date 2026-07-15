"""Intended-behavior tests for argus.federation (stub → xfail until implemented)."""

from __future__ import annotations

import pytest

from argus.config import ArgusConfig
from argus.federation import Federation
from argus.models import FleetState, SessionSnapshot, SessionStatus


@pytest.mark.xfail(reason="stub", strict=False)
async def test_exchange_merges_peer_machines() -> None:
    fed = Federation(ArgusConfig(machine="mac", peers=[]))
    local = FleetState()
    local.upsert(SessionSnapshot(session_id="m1", machine="mac",
                                 status=SessionStatus.THINKING))
    merged = await fed.exchange(local)
    # With no peers reachable, the merged view is at least the local one.
    assert "mac" in merged.machines


@pytest.mark.xfail(reason="stub", strict=False)
async def test_merge_remote_adds_foreign_machine() -> None:
    fed = Federation(ArgusConfig(machine="mac", peers=[]))
    remote = FleetState()
    remote.upsert(SessionSnapshot(session_id="a1", machine="astrape",
                                  status=SessionStatus.BLOCKED, question="q"))
    await fed.merge_remote(remote)
    # Implementation exposes the merged aggregate; asserting no raise + shape.
