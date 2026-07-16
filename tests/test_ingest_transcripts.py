"""Behavior tests for argus.ingest.transcripts (implemented)."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.ingest.transcripts import (
    _read_new_events,
    parse_transcript_file,
    parse_transcript_line,
)
from argus.models import HookEvent


def test_parse_clean_transcript_emits_ordered_events(clean_transcript: Path) -> None:
    events = parse_transcript_file(clean_transcript, machine="mac")
    assert events
    assert all(e.session_id == "aaaaaaaa-0000-4000-8000-000000000001" for e in events)
    assert all(e.machine == "mac" for e in events)
    assert [e.ts for e in events] == sorted(e.ts for e in events)
    # Tool-use lines produce events carrying the tool name.
    tool_names = {e.tool_name for e in events if e.tool_name}
    assert {"Read", "Edit"} <= tool_names


def test_parse_blocked_transcript_carries_notification(
    blocked_transcript: Path,
) -> None:
    events = parse_transcript_file(blocked_transcript, machine="mac")
    assert any("migration" in str(e.raw).lower() for e in events)
    # The permission prompt surfaces as a Notification event.
    assert any(
        e.hook_event_name == HookEvent.NOTIFICATION for e in events
    )


def test_parse_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_transcript_file(tmp_path / "nope.jsonl", machine="mac")


def test_parse_line_skips_non_relevant_types() -> None:
    for ltype in ("mode", "permission-mode", "file-history-snapshot"):
        line = {"type": ltype, "sessionId": "s", "timestamp": "2026-07-15T10:00:00Z"}
        assert parse_transcript_line(line, machine="mac") is None
    # Assistant text-only turns are not a hook-model transition.
    text_only = {
        "type": "assistant",
        "sessionId": "s",
        "timestamp": "2026-07-15T10:00:00Z",
        "message": {"content": [{"type": "text", "text": "done"}]},
    }
    assert parse_transcript_line(text_only, machine="mac") is None
    # A line without a session id cannot be attributed.
    assert parse_transcript_line({"type": "user", "message": {}}, machine="mac") is None


def test_parse_line_maps_tool_use_to_pre_tool_use() -> None:
    line = {
        "type": "assistant",
        "sessionId": "s",
        "cwd": "/home/dev/example",
        "timestamp": "2026-07-15T10:00:01Z",
        "message": {
            "content": [
                {"type": "text", "text": "reading"},
                {"type": "tool_use", "name": "Read", "input": {"file_path": "/x"}},
            ]
        },
    }
    event = parse_transcript_line(line, machine="mac")
    assert event is not None
    assert event.hook_event_name == HookEvent.PRE_TOOL_USE
    assert event.tool_name == "Read"
    assert event.tool_input == {"file_path": "/x"}
    assert event.cwd == "/home/dev/example"


def test_events_follow_transcript_order_and_hook_mapping(
    tool_heavy_transcript: Path,
) -> None:
    events = parse_transcript_file(tool_heavy_transcript, machine="rig")
    # File order is preserved and timestamps are non-decreasing.
    assert [e.ts for e in events] == sorted(e.ts for e in events)
    # First relevant line is the user's prompt; last is session end.
    assert events[0].hook_event_name == HookEvent.USER_PROMPT_SUBMIT
    assert events[-1].hook_event_name == HookEvent.SESSION_END
    # The tool-dense session exposes each distinct tool it invoked.
    tool_names = [e.tool_name for e in events if e.tool_name]
    assert {"Read", "Edit", "Write", "Bash", "Grep"} <= set(tool_names)
    # Tool calls and their results interleave (PreToolUse then PostToolUse).
    kinds = [e.hook_event_name for e in events]
    assert HookEvent.PRE_TOOL_USE in kinds
    assert HookEvent.POST_TOOL_USE in kinds


def test_watch_offset_reads_only_appended_lines(tmp_path: Path) -> None:
    path = tmp_path / "live.jsonl"
    offsets: dict[Path, int] = {}

    line_a = (
        '{"type": "user", "sessionId": "s", "timestamp": "2026-07-15T10:00:00Z", '
        '"message": {"content": [{"type": "text", "text": "hi"}]}}\n'
    )
    line_b = (
        '{"type": "assistant", "sessionId": "s", "timestamp": "2026-07-15T10:00:01Z", '
        '"message": {"content": [{"type": "tool_use", "name": "Read", "input": {}}]}}\n'
    )

    path.write_text(line_a, encoding="utf-8")
    first = _read_new_events(path, offsets, "mac")
    assert [e.hook_event_name for e in first] == [HookEvent.USER_PROMPT_SUBMIT]
    off_after_first = offsets[path]
    assert off_after_first == len(line_a.encode("utf-8"))

    # No new bytes -> nothing re-emitted, offset unchanged.
    assert _read_new_events(path, offsets, "mac") == []
    assert offsets[path] == off_after_first

    # Append a complete line -> only the new line parses.
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line_b)
    second = _read_new_events(path, offsets, "mac")
    assert [e.tool_name for e in second] == ["Read"]
    assert offsets[path] == len((line_a + line_b).encode("utf-8"))


def test_watch_offset_defers_partial_lines(tmp_path: Path) -> None:
    path = tmp_path / "partial.jsonl"
    offsets: dict[Path, int] = {}

    # A half-written line (no trailing newline) is not consumed yet.
    path.write_text('{"type": "user", "sessionId": "s"', encoding="utf-8")
    assert _read_new_events(path, offsets, "mac") == []
    assert offsets.get(path, 0) == 0

    # Once the line is completed, it parses exactly once.
    full = (
        '{"type": "user", "sessionId": "s", "timestamp": "2026-07-15T10:00:00Z", '
        '"message": {"content": [{"type": "text", "text": "hi"}]}}\n'
    )
    path.write_text(full, encoding="utf-8")
    events = _read_new_events(path, offsets, "mac")
    assert len(events) == 1
    assert events[0].session_id == "s"
