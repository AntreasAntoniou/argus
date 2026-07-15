"""Intended-behavior tests for argus.store (stub → xfail until implemented)."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.models import Event, HookEvent
from argus.store import SessionStore


@pytest.mark.xfail(reason="stub", strict=False)
def test_append_then_get_roundtrips(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    store.append(Event(session_id="s1", machine="mac",
                       hook_event_name=HookEvent.SESSION_START,
                       cwd="/home/dev/example"))
    snap = store.get("s1")
    assert snap is not None and snap.machine == "mac"


@pytest.mark.xfail(reason="stub", strict=False)
def test_journal_survives_restart(tmp_path: Path) -> None:
    journal = tmp_path / "j.sqlite3"
    store = SessionStore(journal, machine="mac")
    store.append(Event(session_id="s1", machine="mac",
                       hook_event_name=HookEvent.SESSION_START))
    store.close()

    revived = SessionStore(journal, machine="mac")
    revived.recover()
    assert revived.get("s1") is not None
    assert len(revived.events_for("s1")) == 1
