"""Intended-behavior tests for argus.timeline (stub → xfail until implemented)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from argus.models import Event, HookEvent, TimelineKind
from argus.timeline import build_timeline, parse_transcript_timeline


def test_clean_transcript_yields_tool_and_lifecycle_rows(
    clean_transcript: Path,
) -> None:
    rows = parse_transcript_timeline(clean_transcript)
    kinds = {r.kind for r in rows}
    assert TimelineKind.TOOL in kinds
    assert TimelineKind.LIFECYCLE in kinds
    # Rows ordered by timestamp ascending.
    assert [r.ts for r in rows] == sorted(r.ts for r in rows)
    # The Read and Edit tool calls should both surface.
    summaries = " ".join(r.summary for r in rows)
    assert "Read" in summaries and "Edit" in summaries


def test_blocked_transcript_surfaces_question(blocked_transcript: Path) -> None:
    rows = parse_transcript_timeline(blocked_transcript)
    questions = [r for r in rows if r.kind is TimelineKind.QUESTION]
    assert questions and "migration" in questions[0].summary.lower()


def test_tool_heavy_transcript_counts_and_tokens(tool_heavy_transcript: Path) -> None:
    rows = parse_transcript_timeline(tool_heavy_transcript)
    tool_rows = [r for r in rows if r.kind is TimelineKind.TOOL]
    assert len(tool_rows) >= 6  # six tool calls in the fixture
    token_rows = [r for r in rows if r.kind is TimelineKind.TOKENS]
    assert token_rows  # token accumulation is tracked


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_transcript_timeline(tmp_path / "nope.jsonl")


def test_clean_transcript_emits_file_rows_with_line_deltas(
    clean_transcript: Path,
) -> None:
    rows = parse_transcript_timeline(clean_transcript)
    file_rows = [r for r in rows if r.kind is TimelineKind.FILE]
    # The Edit of mod.py must surface as a FILE row with a +/- line delta.
    assert file_rows
    edit_row = next(r for r in file_rows if "mod.py" in r.summary)
    assert edit_row.added_lines == 1 and edit_row.removed_lines == 1


def test_tool_heavy_transcript_marks_test_run(tool_heavy_transcript: Path) -> None:
    rows = parse_transcript_timeline(tool_heavy_transcript)
    test_rows = [r for r in rows if r.kind is TimelineKind.TEST]
    # `pytest -q` is a test run; its result ("3 passed") lands in the detail.
    assert test_rows
    assert any("passed" in r.detail for r in test_rows)


def test_blocked_question_text_captured_verbatim(blocked_transcript: Path) -> None:
    rows = parse_transcript_timeline(blocked_transcript)
    questions = [r for r in rows if r.kind is TimelineKind.QUESTION]
    assert len(questions) == 1
    assert questions[0].summary == "Run db migration? (y/n)"


def _ev(name: str, ts: str, **kw: object) -> Event:
    return Event(
        session_id="s1",
        machine="local",
        hook_event_name=name,
        ts=datetime.fromisoformat(ts).replace(tzinfo=UTC),
        **kw,  # type: ignore[arg-type]
    )


def test_build_timeline_events_only_no_transcript() -> None:
    events = [
        _ev(HookEvent.SESSION_START, "2026-07-15T09:00:00"),
        _ev(
            HookEvent.PRE_TOOL_USE,
            "2026-07-15T09:00:01",
            tool_name="Read",
            tool_input={"file_path": "/x/a.py"},
        ),
        _ev(
            HookEvent.NOTIFICATION,
            "2026-07-15T09:00:02",
            raw={"notification": "Proceed? (y/n)"},
        ),
        _ev(HookEvent.SESSION_END, "2026-07-15T09:00:03"),
    ]
    rows = build_timeline(events, transcript_path=None)
    kinds = [r.kind for r in rows]
    assert TimelineKind.TOOL in kinds
    assert TimelineKind.QUESTION in kinds
    assert TimelineKind.LIFECYCLE in kinds
    # Ordered by ts ascending.
    assert [r.ts for r in rows] == sorted(r.ts for r in rows)
    question = next(r for r in rows if r.kind is TimelineKind.QUESTION)
    assert question.summary == "Proceed? (y/n)"
    tool = next(r for r in rows if r.kind is TimelineKind.TOOL)
    assert "Read" in tool.summary


def test_build_timeline_prefers_transcript_when_given(
    clean_transcript: Path,
) -> None:
    # With a transcript path, the richer transcript rows drive the result.
    rows = build_timeline([], transcript_path=clean_transcript)
    assert any(r.kind is TimelineKind.TOOL for r in rows)
    assert [r.ts for r in rows] == sorted(r.ts for r in rows)
