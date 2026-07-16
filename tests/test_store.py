"""Behavior tests for argus.store.SessionStore (in-memory map + SQLite journal)."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from argus.models import Event, HookEvent, SessionStatus, utcnow
from argus.store import SessionStore


def test_append_then_get_roundtrips(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    snap = store.append(
        Event(
            session_id="s1",
            machine="mac",
            hook_event_name=HookEvent.SESSION_START,
            cwd="/home/dev/example",
        )
    )
    assert snap.session_id == "s1"
    got = store.get("s1")
    assert got is not None and got.machine == "mac"
    assert got.cwd == "/home/dev/example"
    store.close()


def test_get_unknown_session_is_none(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    assert store.get("nope") is None
    store.close()


def test_append_folds_through_reducer(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    store.append(
        Event(session_id="s1", machine="mac", hook_event_name=HookEvent.SESSION_START)
    )
    snap = store.append(
        Event(
            session_id="s1",
            machine="mac",
            hook_event_name=HookEvent.PRE_TOOL_USE,
            tool_name="Bash",
        )
    )
    assert snap.status is SessionStatus.TOOL
    assert snap.tool_name == "Bash"
    store.close()


def test_journal_survives_restart(tmp_path: Path) -> None:
    journal = tmp_path / "j.sqlite3"
    store = SessionStore(journal, machine="mac")
    store.append(
        Event(session_id="s1", machine="mac", hook_event_name=HookEvent.SESSION_START)
    )
    store.close()

    revived = SessionStore(journal, machine="mac")
    revived.recover()
    assert revived.get("s1") is not None
    assert len(revived.events_for("s1")) == 1
    revived.close()


def test_recover_replays_in_timestamp_order(tmp_path: Path) -> None:
    journal = tmp_path / "j.sqlite3"
    store = SessionStore(journal, machine="mac")
    base = utcnow()
    store.append(
        Event(
            session_id="s1",
            machine="mac",
            hook_event_name=HookEvent.SESSION_START,
            ts=base,
        )
    )
    store.append(
        Event(
            session_id="s1",
            machine="mac",
            hook_event_name=HookEvent.NOTIFICATION,
            ts=base + timedelta(seconds=1),
            raw={"message": "Need permission to run tests?"},
        )
    )
    store.close()

    revived = SessionStore(journal, machine="mac")
    revived.recover()
    snap = revived.get("s1")
    assert snap is not None
    assert snap.status is SessionStatus.BLOCKED
    assert snap.question == "Need permission to run tests?"
    revived.close()


def test_journal_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "journal.sqlite3"
    store = SessionStore(nested, machine="mac")
    assert nested.parent.is_dir()
    store.close()


def test_events_for_returns_oldest_first(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    base = utcnow()
    hooks = [
        HookEvent.SESSION_START,
        HookEvent.USER_PROMPT_SUBMIT,
        HookEvent.PRE_TOOL_USE,
        HookEvent.POST_TOOL_USE,
    ]
    for i, hook in enumerate(hooks):
        is_tool = "Tool" in hook
        store.append(
            Event(
                session_id="s1",
                machine="mac",
                hook_event_name=hook,
                ts=base + timedelta(seconds=i),
                tool_name="Read" if is_tool else None,
                tool_input={"path": "/x"} if is_tool else None,
            )
        )
    # An unrelated session's events must not leak in.
    store.append(
        Event(session_id="s2", machine="mac", hook_event_name=HookEvent.SESSION_START)
    )

    events = store.events_for("s1")
    assert [e.hook_event_name for e in events] == [str(h) for h in hooks]
    assert all(e.session_id == "s1" for e in events)
    # Round-trip fidelity of structured payloads.
    pre = events[2]
    assert pre.tool_name == "Read"
    assert pre.tool_input == {"path": "/x"}
    store.close()


def test_events_for_unknown_session_is_empty(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    assert store.events_for("ghost") == []
    store.close()


def test_local_fleet_tags_machine_and_buckets(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    # A blocked session -> needs_you.
    store.append(
        Event(
            session_id="blocked",
            machine="mac",
            hook_event_name=HookEvent.NOTIFICATION,
            raw={"message": "proceed?"},
        )
    )
    # A working (tool) session -> working.
    store.append(
        Event(
            session_id="busy",
            machine="mac",
            hook_event_name=HookEvent.PRE_TOOL_USE,
            tool_name="Bash",
        )
    )
    # An idle session -> quiet.
    store.append(
        Event(session_id="idle", machine="mac", hook_event_name=HookEvent.STOP)
    )

    fleet = store.local_fleet()
    assert set(fleet.machines) == {"mac"}
    assert len(fleet.all_sessions()) == 3

    buckets = fleet.bucketed()
    assert [s.session_id for s in buckets.needs_you] == ["blocked"]
    assert [s.session_id for s in buckets.working] == ["busy"]
    assert [s.session_id for s in buckets.quiet] == ["idle"]
    store.close()


def test_snapshots_returns_all_live(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "j.sqlite3", machine="mac")
    store.append(
        Event(session_id="a", machine="mac", hook_event_name=HookEvent.SESSION_START)
    )
    store.append(
        Event(session_id="b", machine="mac", hook_event_name=HookEvent.SESSION_START)
    )
    assert {s.session_id for s in store.snapshots()} == {"a", "b"}
    store.close()
