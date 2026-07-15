"""Transcript watcher — tail JSONL transcripts into Events (catch-all path).

STUB — precise typed contract only. Implementers: use ``watchfiles`` to watch
``claude_projects_root/**/*.jsonl`` and emit :class:`argus.models.Event` objects
as lines are appended, so sessions started WITHOUT hooks still appear and
timelines get backfilled (``DESIGN.md`` decision #1).

:func:`parse_transcript_file` is the one-shot, side-effect-free parser tests
drive against fixtures; the watcher is the long-running effectful wrapper around
the same per-line logic.

JSONL schema (learned from real transcripts):
    - line ``type``: ``user`` / ``assistant`` / ``system`` / ``file-history-snapshot``
      / ``mode`` / ``permission-mode`` / ``bridge-session`` / ``queue-operation``
    - ``timestamp``, ``sessionId``, ``cwd``, ``gitBranch`` at line level
    - assistant ``message.content[]``: ``thinking`` / ``text`` / ``tool_use``
    - user ``message.content[]``: ``tool_result``
    - assistant ``message.usage`` for tokens
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable
from pathlib import Path
from typing import Any

from argus.models import Event


def parse_transcript_line(line: dict[str, Any], *, machine: str) -> Event | None:
    """Convert one decoded JSONL line into an :class:`Event`, or skip it.

    Args:
        line: A single decoded JSONL object.
        machine: This node's hostname, stamped onto the event.

    Returns:
        An :class:`Event` for meaningful lines (``user`` / ``assistant`` with
        tool_use / ``system`` lifecycle), or ``None`` for lines with no state
        relevance (``mode`` / ``permission-mode`` / ``file-history-snapshot``).
    """

    raise NotImplementedError("Map one JSONL line to an Event or None")


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

    raise NotImplementedError("Read file, parse each line, collect non-None Events")


async def watch_transcripts(
    roots: Iterable[Path], *, machine: str
) -> AsyncIterator[Event]:
    """Async-iterate Events as JSONL files under ``roots`` grow.

    Uses ``watchfiles.awatch`` to detect appends and yields newly-parsed Events.
    Tracks per-file read offsets so only new lines are parsed.

    Args:
        roots: Directories to watch recursively (typically
            ``[config.paths.claude_projects_root]``).
        machine: Hostname to stamp onto emitted events.

    Yields:
        Newly-observed :class:`Event` objects.
    """

    raise NotImplementedError("watchfiles.awatch over roots, tail-parse new lines")
    # `yield` marks this a generator for typing purposes; unreachable past raise.
    yield  # type: ignore[unreachable]
