"""Intended-behavior tests for argus.reducer (stub → xfail until implemented)."""

from __future__ import annotations

from datetime import timedelta

import pytest

from argus.config import Thresholds
from argus.models import Event, HookEvent, SessionSnapshot, SessionStatus, utcnow
from argus.reducer import is_dead, reduce


def _ev(name: str, **kw) -> Event:
    return Event(session_id="s1", machine="mac", hook_event_name=name, **kw)


@pytest.mark.xfail(reason="stub", strict=False)
def test_first_event_creates_starting_snapshot() -> None:
    snap = reduce(None, _ev(HookEvent.SESSION_START, cwd="/home/dev/example"))
    assert snap.status is SessionStatus.STARTING
    assert snap.cwd == "/home/dev/example"


@pytest.mark.xfail(reason="stub", strict=False)
def test_prompt_then_tool_then_post_transitions() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(s, _ev(HookEvent.USER_PROMPT_SUBMIT))
    assert s.status is SessionStatus.THINKING
    s = reduce(s, _ev(HookEvent.PRE_TOOL_USE, tool_name="Edit"))
    assert s.status is SessionStatus.TOOL and s.tool_name == "Edit"
    s = reduce(s, _ev(HookEvent.POST_TOOL_USE, tool_name="Edit"))
    assert s.status is SessionStatus.THINKING
    assert s.tool_name is None and s.last_tool == "Edit"


@pytest.mark.xfail(reason="stub", strict=False)
def test_notification_enters_blocked_with_question() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(s, _ev(HookEvent.NOTIFICATION,
                      raw={"notification": "Push to main? (y/n)"}))
    assert s.status is SessionStatus.BLOCKED
    assert s.question == "Push to main? (y/n)"


@pytest.mark.xfail(reason="stub", strict=False)
def test_session_end_is_done() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_END))
    assert s.status is SessionStatus.DONE


@pytest.mark.xfail(reason="stub", strict=False)
def test_is_dead_when_pane_gone_and_not_done() -> None:
    snap = SessionSnapshot(session_id="s1", machine="mac",
                           status=SessionStatus.THINKING)
    assert is_dead(snap, now=utcnow(), thresholds=Thresholds(),
                   pane_alive=False, last_jsonl_activity=utcnow())


@pytest.mark.xfail(reason="stub", strict=False)
def test_is_dead_when_jsonl_silent_past_threshold() -> None:
    snap = SessionSnapshot(session_id="s1", machine="mac",
                           status=SessionStatus.THINKING)
    now = utcnow()
    silent_since = now - timedelta(seconds=120)
    assert is_dead(snap, now=now, thresholds=Thresholds(jsonl_silent_seconds=45),
                   pane_alive=True, last_jsonl_activity=silent_since)


@pytest.mark.xfail(reason="stub", strict=False)
def test_done_session_is_not_dead() -> None:
    snap = SessionSnapshot(session_id="s1", machine="mac", status=SessionStatus.DONE)
    assert not is_dead(snap, now=utcnow(), thresholds=Thresholds(),
                       pane_alive=False, last_jsonl_activity=None)
