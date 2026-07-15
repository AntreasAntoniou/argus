"""Intended-behavior tests for argus.ingest.transcripts (stub → xfail)."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.ingest.transcripts import parse_transcript_file


@pytest.mark.xfail(reason="stub", strict=False)
def test_parse_clean_transcript_emits_ordered_events(clean_transcript: Path) -> None:
    events = parse_transcript_file(clean_transcript, machine="mac")
    assert events
    assert all(e.session_id == "aaaaaaaa-0000-4000-8000-000000000001" for e in events)
    assert [e.ts for e in events] == sorted(e.ts for e in events)
    # Tool-use lines produce events carrying the tool name.
    tool_names = {e.tool_name for e in events if e.tool_name}
    assert {"Read", "Edit"} <= tool_names


@pytest.mark.xfail(reason="stub", strict=False)
def test_parse_blocked_transcript_carries_notification(
    blocked_transcript: Path,
) -> None:
    events = parse_transcript_file(blocked_transcript, machine="mac")
    assert any("migration" in str(e.raw).lower() for e in events)


@pytest.mark.xfail(reason="stub", strict=False)
def test_parse_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        parse_transcript_file(tmp_path / "nope.jsonl", machine="mac")
