"""Intended-behavior tests for argus.timeline (stub → xfail until implemented)."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.models import TimelineKind
from argus.timeline import parse_transcript_timeline


@pytest.mark.xfail(reason="stub", strict=False)
def test_clean_transcript_yields_tool_and_lifecycle_rows(
    clean_transcript: Path,
) -> None:
    rows = parse_transcript_timeline(clean_transcript)
    kinds = {r.kind for r in rows}
    assert TimelineKind.TOOL in kinds
    # Rows ordered by timestamp ascending.
    assert [r.ts for r in rows] == sorted(r.ts for r in rows)
    # The Read and Edit tool calls should both surface.
    summaries = " ".join(r.summary for r in rows)
    assert "Read" in summaries and "Edit" in summaries


@pytest.mark.xfail(reason="stub", strict=False)
def test_blocked_transcript_surfaces_question(blocked_transcript: Path) -> None:
    rows = parse_transcript_timeline(blocked_transcript)
    questions = [r for r in rows if r.kind is TimelineKind.QUESTION]
    assert questions and "migration" in questions[0].summary.lower()


@pytest.mark.xfail(reason="stub", strict=False)
def test_tool_heavy_transcript_counts_and_tokens(tool_heavy_transcript: Path) -> None:
    rows = parse_transcript_timeline(tool_heavy_transcript)
    tool_rows = [r for r in rows if r.kind is TimelineKind.TOOL]
    assert len(tool_rows) >= 6  # six tool calls in the fixture
    token_rows = [r for r in rows if r.kind is TimelineKind.TOKENS]
    assert token_rows  # token accumulation is tracked


@pytest.mark.xfail(reason="stub", strict=False)
def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_transcript_timeline(tmp_path / "nope.jsonl")
