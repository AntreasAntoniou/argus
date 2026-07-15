"""Diff layer — read-only git status/diff for a session's cwd, cached with TTL.

STUB — precise typed contract only. Implementers: implement ``DESIGN.md``
decision #9 (read-only ``git status`` / ``diff --stat`` of each session's actual
cwd + branch; no imposed worktree convention). Cache per (cwd, branch) with a
short TTL so the board can call this on every render cheaply.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, slots=True)
class DiffStat:
    """Summary of the working-tree diff for a session's cwd.

    Attributes:
        branch: Current git branch of the cwd.
        files_changed: Number of files with changes.
        added_lines: Total lines added (``diff --stat`` insertions).
        removed_lines: Total lines removed (``diff --stat`` deletions).
        dirty: Whether the working tree has uncommitted changes.
        detail: Raw ``git diff --stat`` text for the drill-down pane.
    """

    branch: str | None
    files_changed: int = 0
    added_lines: int = 0
    removed_lines: int = 0
    dirty: bool = False
    detail: str = ""


@dataclass(slots=True)
class DiffCache:
    """TTL cache of :class:`DiffStat` keyed by cwd, backed by git subprocess calls."""

    ttl_seconds: float = 3.0
    _entries: dict[str, tuple[float, DiffStat]] = field(default_factory=dict)

    def get(self, cwd: Path) -> DiffStat:
        """Return a (possibly cached) :class:`DiffStat` for a session's cwd.

        Runs ``git status`` / ``git diff --stat`` in ``cwd`` on a cache miss or
        when the cached entry is older than ``ttl_seconds``.

        Args:
            cwd: The session's working directory.

        Returns:
            The current :class:`DiffStat`. A non-git or missing ``cwd`` yields a
            benign empty stat (``branch=None``), never an exception.
        """

        raise NotImplementedError("TTL-cached git status/diff --stat for cwd")

    def invalidate(self, cwd: Path | None = None) -> None:
        """Drop cached entries (all, or just ``cwd``)."""

        raise NotImplementedError("Evict cache entries")
