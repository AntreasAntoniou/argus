"""Transcript watcher — tail JSONL transcripts into Events (catch-all path).

Turns Claude Code ``*.jsonl`` transcripts into :class:`argus.models.Event`
objects so sessions started WITHOUT hooks still appear on the board and
timelines get backfilled (``DESIGN.md`` decision #1).

:func:`parse_transcript_line` maps a single decoded JSONL object to at most one
Event; :func:`parse_transcript_file` is the one-shot, side-effect-free parser the
fixture tests drive against; :func:`watch_transcripts` is the long-running
effectful wrapper that tails appended lines via ``watchfiles.awatch`` around the
same per-line logic, tracking a per-file byte offset so only new lines re-emit.

JSONL schema (learned from real transcripts):
    - line ``type``: ``user`` / ``assistant`` / ``system`` / ``file-history-snapshot``
      / ``mode`` / ``permission-mode`` / ``bridge-session`` / ``queue-operation``
    - ``timestamp``, ``sessionId``, ``cwd``, ``gitBranch`` at line level
    - assistant ``message.content[]``: ``thinking`` / ``text`` / ``tool_use``
    - user ``message.content[]``: ``tool_result``
    - system ``subtype``: ``notification`` (permission prompt) / ``session_end``
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from watchfiles import awatch

from argus.models import Event, HookEvent, utcnow

# Line ``type`` values with no state relevance — skipped outright.
_SKIP_TYPES = frozenset({"mode", "permission-mode", "file-history-snapshot"})


def _parse_ts(value: Any) -> datetime:
    """Parse an ISO-8601 transcript timestamp into a tz-aware UTC datetime."""

    if not isinstance(value, str):
        return utcnow()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return utcnow()
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _content_items(message: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the ``message.content`` list, or ``[]`` for string/absent content."""

    content = message.get("content")
    if not isinstance(content, list):
        return []
    return [i for i in content if isinstance(i, dict)]


def parse_transcript_line(line: dict[str, Any], *, machine: str) -> Event | None:
    """Convert one decoded JSONL line into an :class:`Event`, or skip it.

    Args:
        line: A single decoded JSONL object.
        machine: This node's hostname, stamped onto the event.

    Returns:
        An :class:`Event` for state-relevant lines (a user prompt, a tool call,
        a tool result, a notification/permission prompt, or session end), or
        ``None`` for lines with no state relevance (``mode`` / ``permission-mode``
        / ``file-history-snapshot``, assistant text-only messages, other
        ``system`` subtypes). Assistant lines carrying a ``tool_use`` yield a
        ``PreToolUse`` event stamped with the first tool's ``name`` / ``input``.
    """

    ltype = line.get("type")
    if ltype in _SKIP_TYPES:
        return None

    session_id = line.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        return None

    ts = _parse_ts(line.get("timestamp"))
    cwd = line.get("cwd") if isinstance(line.get("cwd"), str) else None

    if ltype == "assistant":
        message = line.get("message") or {}
        for item in _content_items(message):
            if item.get("type") == "tool_use":
                return Event(
                    session_id=session_id,
                    machine=machine,
                    hook_event_name=HookEvent.PRE_TOOL_USE,
                    ts=ts,
                    cwd=cwd,
                    tool_name=item.get("name"),
                    tool_input=item.get("input"),
                    raw=line,
                )
        # Text/thinking-only assistant turn: no hook-model transition.
        return None

    if ltype == "user":
        message = line.get("message") or {}
        items = _content_items(message)
        has_result = any(i.get("type") == "tool_result" for i in items)
        name = HookEvent.POST_TOOL_USE if has_result else HookEvent.USER_PROMPT_SUBMIT
        return Event(
            session_id=session_id,
            machine=machine,
            hook_event_name=name,
            ts=ts,
            cwd=cwd,
            raw=line,
        )

    if ltype == "system":
        subtype = line.get("subtype")
        if subtype == "notification":
            hook = HookEvent.NOTIFICATION
        elif subtype == "session_end":
            hook = HookEvent.SESSION_END
        else:
            return None
        return Event(
            session_id=session_id,
            machine=machine,
            hook_event_name=hook,
            ts=ts,
            cwd=cwd,
            raw=line,
        )

    return None


def _event_from_raw_line(raw: str, machine: str) -> Event | None:
    """Decode one raw JSONL text line into an Event, tolerating blanks/garbage."""

    raw = raw.strip()
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    return parse_transcript_line(obj, machine=machine)


def parse_transcript_file(path: Path, *, machine: str = "local") -> list[Event]:
    """Parse a whole JSONL transcript into an ordered list of Events.

    Side-effect-free one-shot parser (the path tests drive against fixtures).

    Args:
        path: Path to a ``*.jsonl`` transcript.
        machine: Hostname to stamp onto emitted events.

    Returns:
        Events in file order (skipping non-relevant lines).

    Raises:
        FileNotFoundError: If ``path`` does not exist.
    """

    events: list[Event] = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for raw in handle:
            event = _event_from_raw_line(raw, machine)
            if event is not None:
                events.append(event)
    return events


def _read_new_events(
    path: Path, offsets: dict[Path, int], machine: str
) -> list[Event]:
    """Parse only the bytes appended to ``path`` since the last read.

    Reads from the stored byte offset to end-of-file, consumes only *complete*
    lines (up to the final newline) so a half-written appended line is left for
    the next call, advances ``offsets[path]``, and returns the parsed Events.
    Pure enough to unit-test the watcher's tail logic without a live loop.
    """

    start = offsets.get(path, 0)
    try:
        with path.open("rb") as handle:
            handle.seek(start)
            data = handle.read()
    except FileNotFoundError:
        offsets.pop(path, None)
        return []

    if not data:
        return []

    last_newline = data.rfind(b"\n")
    if last_newline == -1:
        # No complete line yet; leave the offset untouched.
        return []

    complete = data[: last_newline + 1]
    offsets[path] = start + len(complete)

    events: list[Event] = []
    for raw in complete.decode("utf-8", errors="replace").splitlines():
        event = _event_from_raw_line(raw, machine)
        if event is not None:
            events.append(event)
    return events


async def watch_transcripts(
    roots: Iterable[Path], *, machine: str
) -> AsyncIterator[Event]:
    """Async-iterate Events as JSONL files under ``roots`` grow.

    Backfills every existing ``*.jsonl`` under ``roots`` once, then uses
    ``watchfiles.awatch`` to detect appends and yields newly-parsed Events.
    Tracks per-file byte offsets so only lines added since the last read parse.

    Args:
        roots: Directories to watch recursively (typically
            ``[config.paths.claude_projects_root]``).
        machine: Hostname to stamp onto emitted events.

    Yields:
        Newly-observed :class:`Event` objects.
    """

    root_paths = [Path(r) for r in roots]
    existing = [r for r in root_paths if r.exists()]
    offsets: dict[Path, int] = {}

    # Backfill: emit everything already on disk, priming per-file offsets.
    for root in existing:
        for path in sorted(root.rglob("*.jsonl")):
            for event in _read_new_events(path, offsets, machine):
                yield event

    if not existing:
        return

    async for changes in awatch(*existing):
        # Deterministic order across a batch of file changes.
        for _change, raw_path in sorted(changes, key=lambda c: c[1]):
            path = Path(raw_path)
            if path.suffix != ".jsonl":
                continue
            for event in _read_new_events(path, offsets, machine):
                yield event
