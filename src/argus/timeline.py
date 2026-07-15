"""Semantic timeline builder — collapse events + JSONL into readable rows.

STUB — precise typed contract only. Implementers: derive a
``list[TimelineEntry]`` per agent from the SQLite journal events and/or the raw
JSONL transcript (``DESIGN.md`` §Semantic timeline): tool calls, files touched
with +/- lines, tests run and their results, tokens burned, questions asked.
Rows are collapsed/expandable; the transcript stays the deep source of truth.

The JSONL schema this parses (learned from real transcripts):
    - line ``type``: ``user`` / ``assistant`` / ``system`` / ``file-history-snapshot``
    - ``timestamp``, ``sessionId``, ``cwd``, ``gitBranch`` at line level
    - assistant ``message.content[]`` items: ``thinking`` / ``text`` /
      ``tool_use{name,input,id}``
    - user ``message.content[]`` items: ``tool_result{tool_use_id,content}``
    - assistant ``message.usage.{input_tokens,output_tokens,
      cache_read_input_tokens,cache_creation_input_tokens}``
"""

from __future__ import annotations

from pathlib import Path

from argus.models import Event, TimelineEntry


def build_timeline(
    events: list[Event],
    *,
    transcript_path: Path | None = None,
) -> list[TimelineEntry]:
    """Build the collapsed semantic timeline for one session.

    Merges journalled :class:`~argus.models.Event` objects with the richer JSONL
    transcript (when available) into an ordered, human-readable row list.

    Args:
        events: Journalled events for the session (from
            :meth:`argus.store.SessionStore.events_for`), oldest first.
        transcript_path: Optional path to the session's ``*.jsonl`` for deep
            detail (tool inputs, token usage, file diffs). When ``None``, build
            the best timeline possible from ``events`` alone.

    Returns:
        Timeline rows ordered by timestamp ascending.
    """

    raise NotImplementedError("Merge events + JSONL into ordered TimelineEntry rows")


def parse_transcript_timeline(transcript_path: Path) -> list[TimelineEntry]:
    """Build a timeline purely from a JSONL transcript file.

    One-shot parser (no journal) — the path tests drive directly against
    fixtures. Emits a row per tool call, file touch (with +/- lines when the
    tool input reveals them), test run/result, token checkpoint, and question.

    Args:
        transcript_path: Path to a Claude Code ``*.jsonl`` transcript.

    Returns:
        Timeline rows ordered by timestamp ascending.

    Raises:
        FileNotFoundError: If ``transcript_path`` does not exist.
    """

    raise NotImplementedError("Parse a single JSONL transcript into TimelineEntry rows")
