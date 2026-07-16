"""Semantic timeline builder — collapse events + JSONL into readable rows.

Derives a ``list[TimelineEntry]`` per agent from the SQLite journal events
and/or the raw JSONL transcript (``DESIGN.md`` §Semantic timeline): tool calls,
files touched with +/- lines, tests run and their results, tokens burned, and
questions asked. Rows are collapsed (one-line ``summary``) with an expandable
``detail``; the transcript stays the deep source of truth.

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

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from argus.models import Event, HookEvent, TimelineEntry, TimelineKind

# Tools that mutate a file (emit a FILE row with +/- line deltas).
_MUTATING_FILE_TOOLS = frozenset({"Edit", "Write", "MultiEdit", "NotebookEdit"})
# Substrings that mark a Bash command as a test run.
_TEST_MARKERS = ("pytest", "unittest", "go test", "cargo test", "npm test", "tox")


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO-8601 transcript timestamp into a tz-aware UTC datetime."""

    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=UTC)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _stringify_result(content: Any) -> str:
    """Flatten a ``tool_result`` content payload into plain text."""

    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            item.get("text", "")
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return "\n".join(p for p in parts if p)
    return ""


def _count_lines(text: Any) -> int:
    """Number of lines in a string payload (0 for empty/non-string)."""

    return len(text.splitlines()) if isinstance(text, str) and text else 0


def _tool_target(tool_input: dict[str, Any] | None) -> str:
    """A short, human-readable target for a tool call (file / command / pattern)."""

    if not tool_input:
        return ""
    keys = ("file_path", "path", "notebook_path", "command", "pattern", "query", "url")
    for key in keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _file_deltas(name: str, tool_input: dict[str, Any] | None) -> tuple[int, int]:
    """Return ``(added_lines, removed_lines)`` a mutating tool's input reveals."""

    if not tool_input:
        return (0, 0)
    if name == "Write":
        return (_count_lines(tool_input.get("content")), 0)
    if name in ("Edit", "NotebookEdit"):
        new = tool_input.get("new_string") or tool_input.get("new_source")
        old = tool_input.get("old_string") or tool_input.get("old_source")
        return (_count_lines(new), _count_lines(old))
    if name == "MultiEdit":
        added = removed = 0
        for edit in tool_input.get("edits", []) or []:
            if isinstance(edit, dict):
                added += _count_lines(edit.get("new_string"))
                removed += _count_lines(edit.get("old_string"))
        return (added, removed)
    return (0, 0)


def _is_test_command(command: Any) -> bool:
    """True when a Bash command looks like a test-runner invocation."""

    return isinstance(command, str) and any(m in command for m in _TEST_MARKERS)


def _question_from_raw(raw: dict[str, Any] | None) -> str | None:
    """Extract the pending question text from a notification-ish payload."""

    if not raw:
        return None
    for key in ("notification", "message", "question", "prompt"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


def _rows_for_tool(
    ts: datetime,
    name: str,
    tool_input: dict[str, Any] | None,
    result: str,
) -> list[TimelineEntry]:
    """Emit the TOOL row plus any FILE/TEST row a single tool call implies."""

    target = _tool_target(tool_input)
    summary = f"{name} {target}".strip()
    detail = json.dumps(tool_input, sort_keys=True) if tool_input else ""
    rows = [
        TimelineEntry(ts=ts, kind=TimelineKind.TOOL, summary=summary, detail=detail)
    ]

    if name in _MUTATING_FILE_TOOLS:
        added, removed = _file_deltas(name, tool_input)
        rows.append(
            TimelineEntry(
                ts=ts,
                kind=TimelineKind.FILE,
                summary=summary,
                detail=result,
                added_lines=added,
                removed_lines=removed,
            )
        )
    elif name == "Bash" and _is_test_command((tool_input or {}).get("command")):
        rows.append(
            TimelineEntry(ts=ts, kind=TimelineKind.TEST, summary=summary, detail=result)
        )
    return rows


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

    text = transcript_path.read_text(encoding="utf-8")

    lines: list[dict[str, Any]] = []
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            lines.append(json.loads(stripped))
        except json.JSONDecodeError:
            continue

    # First pass: index tool_result content by the tool_use id it answers.
    results: dict[str, str] = {}
    for line in lines:
        if line.get("type") != "user":
            continue
        for item in (line.get("message") or {}).get("content", []) or []:
            if isinstance(item, dict) and item.get("type") == "tool_result":
                tool_id = item.get("tool_use_id")
                if tool_id:
                    results[tool_id] = _stringify_result(item.get("content"))

    rows: list[TimelineEntry] = []
    cumulative_tokens = 0

    for line in lines:
        ltype = line.get("type")
        ts = _parse_ts(line.get("timestamp"))
        message = line.get("message") or {}

        if ltype == "assistant":
            usage = message.get("usage") or {}
            if usage:
                cumulative_tokens += int(usage.get("input_tokens", 0) or 0)
                cumulative_tokens += int(usage.get("output_tokens", 0) or 0)
                rows.append(
                    TimelineEntry(
                        ts=ts,
                        kind=TimelineKind.TOKENS,
                        summary=f"{cumulative_tokens} tokens",
                        detail=json.dumps(usage, sort_keys=True),
                    )
                )
            for item in message.get("content", []) or []:
                if isinstance(item, dict) and item.get("type") == "tool_use":
                    result = results.get(item.get("id", ""), "")
                    rows.extend(
                        _rows_for_tool(
                            ts, item.get("name", "tool"), item.get("input"), result
                        )
                    )

        elif ltype == "user":
            content = message.get("content", []) or []
            if any(isinstance(i, dict) and i.get("type") == "text" for i in content):
                rows.append(
                    TimelineEntry(
                        ts=ts, kind=TimelineKind.LIFECYCLE, summary="user prompt"
                    )
                )

        elif ltype == "system":
            subtype = line.get("subtype")
            if subtype == "notification":
                question = _question_from_raw(line) or "waiting for input"
                rows.append(
                    TimelineEntry(ts=ts, kind=TimelineKind.QUESTION, summary=question)
                )
            elif subtype == "session_end":
                rows.append(
                    TimelineEntry(
                        ts=ts, kind=TimelineKind.LIFECYCLE, summary="session ended"
                    )
                )

    rows.sort(key=lambda r: r.ts)
    return rows


_LIFECYCLE_SUMMARIES: dict[str, str] = {
    HookEvent.SESSION_START: "session started",
    HookEvent.USER_PROMPT_SUBMIT: "user prompt",
    HookEvent.STOP: "turn finished",
    HookEvent.SUBAGENT_STOP: "subagent finished",
    HookEvent.SESSION_END: "session ended",
}


def _rows_from_events(events: list[Event]) -> list[TimelineEntry]:
    """Best-effort timeline from journalled events alone (no transcript)."""

    rows: list[TimelineEntry] = []
    for event in events:
        name = event.hook_event_name
        if name == HookEvent.PRE_TOOL_USE:
            tool = event.tool_name or "tool"
            rows.extend(_rows_for_tool(event.ts, tool, event.tool_input, ""))
        elif name == HookEvent.NOTIFICATION:
            question = _question_from_raw(event.raw) or "waiting for input"
            rows.append(
                TimelineEntry(ts=event.ts, kind=TimelineKind.QUESTION, summary=question)
            )
        elif name in _LIFECYCLE_SUMMARIES:
            rows.append(
                TimelineEntry(
                    ts=event.ts,
                    kind=TimelineKind.LIFECYCLE,
                    summary=_LIFECYCLE_SUMMARIES[name],
                )
            )
    return rows


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

    rows: list[TimelineEntry] = []
    if transcript_path is not None and transcript_path.exists():
        # The transcript is the deep source of truth; take its rich rows and
        # fold in any event-derived rows the transcript did not already cover.
        rows.extend(parse_transcript_timeline(transcript_path))
        seen = {(r.ts, r.kind, r.summary) for r in rows}
        for row in _rows_from_events(events):
            if (row.ts, row.kind, row.summary) not in seen:
                rows.append(row)
    else:
        rows.extend(_rows_from_events(events))

    rows.sort(key=lambda r: r.ts)
    return rows
