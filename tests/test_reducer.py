"""Intended-behavior tests for argus.reducer (state machine + dead-detection)."""

from __future__ import annotations

from datetime import timedelta

from argus.config import Thresholds
from argus.models import Event, HookEvent, SessionSnapshot, SessionStatus, utcnow
from argus.reducer import extract_question, is_dead, reduce


def _ev(name: str, **kw) -> Event:
    return Event(session_id="s1", machine="mac", hook_event_name=name, **kw)


def test_first_event_creates_starting_snapshot() -> None:
    snap = reduce(None, _ev(HookEvent.SESSION_START, cwd="/home/dev/example"))
    assert snap.status is SessionStatus.STARTING
    assert snap.cwd == "/home/dev/example"


def test_prompt_then_tool_then_post_transitions() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(s, _ev(HookEvent.USER_PROMPT_SUBMIT))
    assert s.status is SessionStatus.THINKING
    s = reduce(s, _ev(HookEvent.PRE_TOOL_USE, tool_name="Edit"))
    assert s.status is SessionStatus.TOOL and s.tool_name == "Edit"
    s = reduce(s, _ev(HookEvent.POST_TOOL_USE, tool_name="Edit"))
    assert s.status is SessionStatus.THINKING
    assert s.tool_name is None and s.last_tool == "Edit"


def test_notification_enters_blocked_with_question() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(
        s, _ev(HookEvent.NOTIFICATION, raw={"notification": "Push to main? (y/n)"})
    )
    assert s.status is SessionStatus.BLOCKED
    assert s.question == "Push to main? (y/n)"


def test_session_end_is_done() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_END))
    assert s.status is SessionStatus.DONE


def test_is_dead_when_pane_gone_and_not_done() -> None:
    snap = SessionSnapshot(
        session_id="s1", machine="mac", status=SessionStatus.THINKING
    )
    assert is_dead(
        snap,
        now=utcnow(),
        thresholds=Thresholds(),
        pane_alive=False,
        last_jsonl_activity=utcnow(),
    )


def test_is_dead_when_jsonl_silent_past_threshold() -> None:
    snap = SessionSnapshot(
        session_id="s1", machine="mac", status=SessionStatus.THINKING
    )
    now = utcnow()
    silent_since = now - timedelta(seconds=120)
    assert is_dead(
        snap,
        now=now,
        thresholds=Thresholds(jsonl_silent_seconds=45),
        pane_alive=True,
        last_jsonl_activity=silent_since,
    )


def test_done_session_is_not_dead() -> None:
    snap = SessionSnapshot(session_id="s1", machine="mac", status=SessionStatus.DONE)
    assert not is_dead(
        snap,
        now=utcnow(),
        thresholds=Thresholds(),
        pane_alive=False,
        last_jsonl_activity=None,
    )


# --- Additional coverage for otherwise-uncovered transitions ---------------


def test_stop_and_subagent_stop_go_idle() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(s, _ev(HookEvent.STOP))
    assert s.status is SessionStatus.IDLE
    s = reduce(s, _ev(HookEvent.USER_PROMPT_SUBMIT))
    s = reduce(s, _ev(HookEvent.SUBAGENT_STOP))
    assert s.status is SessionStatus.IDLE


def test_leaving_blocked_clears_question() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(
        s, _ev(HookEvent.NOTIFICATION, raw={"notification": "Delete file? (y/n)"})
    )
    assert s.status is SessionStatus.BLOCKED and s.question is not None
    s = reduce(s, _ev(HookEvent.PRE_TOOL_USE, tool_name="Bash"))
    assert s.status is SessionStatus.TOOL
    assert s.question is None


def test_prompt_after_blocked_clears_question() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(s, _ev(HookEvent.NOTIFICATION, raw={"notification": "Proceed? (y/n)"}))
    s = reduce(s, _ev(HookEvent.USER_PROMPT_SUBMIT))
    assert s.status is SessionStatus.THINKING
    assert s.question is None


def test_tool_name_set_iff_tool_status() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    assert s.tool_name is None
    s = reduce(s, _ev(HookEvent.PRE_TOOL_USE, tool_name="Read"))
    assert s.status is SessionStatus.TOOL and s.tool_name == "Read"
    # Every non-TOOL transition must null tool_name while preserving last_tool.
    for hook in (
        HookEvent.POST_TOOL_USE,
        HookEvent.USER_PROMPT_SUBMIT,
        HookEvent.STOP,
    ):
        s = reduce(s, _ev(hook))
        assert s.status is not SessionStatus.TOOL
        assert s.tool_name is None
    assert s.last_tool == "Read"


def test_pretooluse_updates_last_tool() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(s, _ev(HookEvent.PRE_TOOL_USE, tool_name="Read"))
    s = reduce(s, _ev(HookEvent.POST_TOOL_USE, tool_name="Read"))
    s = reduce(s, _ev(HookEvent.PRE_TOOL_USE, tool_name="Edit"))
    assert s.tool_name == "Edit" and s.last_tool == "Edit"


def test_terminal_done_does_not_regress_without_restart() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_END))
    assert s.status is SessionStatus.DONE
    # Later events do not un-terminate a finished session...
    s = reduce(s, _ev(HookEvent.PRE_TOOL_USE, tool_name="Edit"))
    assert s.status is SessionStatus.DONE
    assert s.tool_name is None
    # ...but a new SessionStart is an explicit restart.
    s = reduce(s, _ev(HookEvent.SESSION_START))
    assert s.status is SessionStatus.STARTING


def test_updated_at_and_cwd_refresh_even_when_terminal() -> None:
    end = _ev(HookEvent.SESSION_END, cwd="/home/dev/a")
    s = reduce(None, end)
    assert s.updated_at == end.ts and s.cwd == "/home/dev/a"
    later = _ev(HookEvent.STOP, cwd="/home/dev/b")
    s = reduce(s, later)
    assert s.status is SessionStatus.DONE
    assert s.updated_at == later.ts
    assert s.cwd == "/home/dev/b"


def test_updated_at_stamped_from_event_ts() -> None:
    ev = _ev(HookEvent.USER_PROMPT_SUBMIT)
    s = reduce(None, ev)
    assert s.updated_at == ev.ts


def test_extract_question_prefers_notification_key() -> None:
    ev = _ev(HookEvent.NOTIFICATION, raw={"notification": "Q1", "message": "Q2"})
    assert extract_question(ev) == "Q1"


def test_extract_question_none_when_absent_or_blank() -> None:
    assert extract_question(_ev(HookEvent.NOTIFICATION)) is None
    blank = _ev(HookEvent.NOTIFICATION, raw={"notification": "  "})
    assert extract_question(blank) is None


def test_is_dead_pane_alive_and_recent_jsonl_is_live() -> None:
    snap = SessionSnapshot(
        session_id="s1", machine="mac", status=SessionStatus.THINKING
    )
    now = utcnow()
    assert not is_dead(
        snap,
        now=now,
        thresholds=Thresholds(jsonl_silent_seconds=45),
        pane_alive=True,
        last_jsonl_activity=now - timedelta(seconds=5),
    )


def test_dead_snapshot_is_sticky_except_session_start() -> None:
    s = SessionSnapshot(session_id="s1", machine="mac", status=SessionStatus.DEAD)
    s = reduce(s, _ev(HookEvent.USER_PROMPT_SUBMIT))
    assert s.status is SessionStatus.DEAD
    s = reduce(s, _ev(HookEvent.SESSION_START))
    assert s.status is SessionStatus.STARTING


def test_synthetic_event_preserves_tool_name_invariant() -> None:
    s = reduce(None, _ev(HookEvent.SESSION_START))
    s = reduce(s, _ev(HookEvent.USER_PROMPT_SUBMIT))
    s.tool_name = "Edit"
    s = reduce(s, _ev("transcript"))
    assert s.status is SessionStatus.THINKING
    assert s.tool_name is None
