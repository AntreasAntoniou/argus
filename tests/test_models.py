"""Tests for argus.models — the implemented domain contract. These MUST pass."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta

import pytest

from argus.models import (
    Buckets,
    Event,
    FleetState,
    HookEvent,
    SessionSnapshot,
    SessionStatus,
    TimelineEntry,
    TimelineKind,
    utcnow,
)


def test_session_status_values_match_design() -> None:
    # Wire values must match DESIGN.md's lowercase state-machine tokens exactly.
    assert SessionStatus.STARTING == "starting"
    assert SessionStatus.THINKING == "thinking"
    assert SessionStatus.TOOL == "tool"
    assert SessionStatus.BLOCKED == "blocked"
    assert SessionStatus.IDLE == "idle"
    assert SessionStatus.DONE == "done"
    assert SessionStatus.DEAD == "dead"
    assert {s.value for s in SessionStatus} == {
        "starting", "thinking", "tool", "blocked", "idle", "done", "dead"
    }


def test_hook_event_covers_eight_lifecycle_hooks() -> None:
    assert len(list(HookEvent)) == 8
    assert HookEvent.PRE_TOOL_USE == "PreToolUse"
    assert HookEvent.SESSION_END == "SessionEnd"


def test_event_is_frozen_and_defaults() -> None:
    ev = Event(session_id="s1", machine="mac", hook_event_name=HookEvent.SESSION_START)
    assert ev.tool_name is None and ev.raw is None
    assert ev.ts.tzinfo is not None  # utcnow() is tz-aware
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.session_id = "other"  # type: ignore[misc]


def test_utcnow_is_timezone_aware_utc() -> None:
    assert utcnow().tzinfo == UTC


def test_snapshot_label_and_flags() -> None:
    s = SessionSnapshot(session_id="s1", machine="mac", status=SessionStatus.TOOL,
                        tool_name="Edit")
    assert s.label() == "tool:Edit"
    assert not s.needs_you and not s.is_terminal

    blocked = SessionSnapshot(session_id="s2", machine="mac",
                              status=SessionStatus.BLOCKED, question="Push? (y/n)")
    assert blocked.needs_you and blocked.label() == "blocked"

    done = SessionSnapshot(session_id="s3", machine="mac", status=SessionStatus.DONE)
    dead = SessionSnapshot(session_id="s4", machine="mac", status=SessionStatus.DEAD)
    assert done.is_terminal and dead.is_terminal


def test_timeline_entry_fields() -> None:
    e = TimelineEntry(ts=utcnow(), kind=TimelineKind.FILE, summary="edit mod.py",
                      detail="...", added_lines=10, removed_lines=3)
    assert e.kind == "file" and e.added_lines == 10
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.summary = "x"  # type: ignore[misc]


def test_fleet_upsert_replaces_by_session_id() -> None:
    fleet = FleetState()
    fleet.upsert(SessionSnapshot(session_id="s1", machine="mac",
                                 status=SessionStatus.THINKING))
    fleet.upsert(SessionSnapshot(session_id="s1", machine="mac",
                                 status=SessionStatus.DONE))
    assert len(fleet.machines["mac"]) == 1
    assert fleet.machines["mac"][0].status is SessionStatus.DONE


def test_fleet_merge_replaces_remote_machine_wholesale() -> None:
    local = FleetState()
    local.upsert(SessionSnapshot(session_id="m1", machine="mac",
                                 status=SessionStatus.THINKING))
    remote = FleetState()
    remote.upsert(SessionSnapshot(session_id="a1", machine="astrape",
                                  status=SessionStatus.BLOCKED, question="q"))
    local.merge(remote)
    assert set(local.machines) == {"mac", "astrape"}
    assert len(local.all_sessions()) == 2


def test_bucketed_sorts_needs_you_working_quiet() -> None:
    now = datetime(2026, 7, 15, tzinfo=UTC)
    fleet = FleetState()
    # Two blocked with different ages -> oldest question floats to top.
    fleet.upsert(SessionSnapshot(session_id="b_new", machine="mac",
                                 status=SessionStatus.BLOCKED, question="new",
                                 updated_at=now))
    fleet.upsert(SessionSnapshot(session_id="b_old", machine="mac",
                                 status=SessionStatus.BLOCKED, question="old",
                                 updated_at=now - timedelta(minutes=5)))
    fleet.upsert(SessionSnapshot(session_id="w1", machine="mac",
                                 status=SessionStatus.TOOL, tool_name="Bash",
                                 updated_at=now))
    fleet.upsert(SessionSnapshot(session_id="q1", machine="mac",
                                 status=SessionStatus.DONE, updated_at=now))
    fleet.upsert(SessionSnapshot(session_id="d1", machine="mac",
                                 status=SessionStatus.DEAD, updated_at=now))

    buckets = fleet.bucketed()
    assert isinstance(buckets, Buckets)
    assert [s.session_id for s in buckets.needs_you] == ["b_old", "b_new"]
    assert [s.session_id for s in buckets.working] == ["w1"]
    assert {s.session_id for s in buckets.quiet} == {"q1", "d1"}
