"""Diff layer — read-only git status/diff for a session's cwd, cached with TTL.

Implements ``DESIGN.md`` decision #9: read-only ``git status`` / ``diff --stat``
of each session's actual cwd + branch (no imposed worktree convention). Results
are cached per cwd with a short TTL so the board can call this on every render
cheaply. A non-git or missing cwd yields a benign empty stat and never raises.
"""

from __future__ import annotations

import subprocess
import time
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


def _run_git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a git subcommand in ``cwd`` read-only; ``None`` if git/cwd is unusable."""

    try:
        return subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError, ValueError:
        return None


def _parse_branch(header: str) -> str | None:
    """Extract the branch name from a ``git status --branch`` header line."""

    body = header[3:].strip()  # drop the leading "## "
    if body.startswith("No commits yet on "):
        return body[len("No commits yet on ") :].strip() or None
    if body.startswith("HEAD (no branch)"):
        return None
    body = body.split("...", 1)[0]
    body = body.split(" ", 1)[0]
    return body or None


def _sum_numstat(text: str) -> tuple[int, int]:
    """Sum insertions/deletions across ``git diff --numstat`` output lines."""

    added = removed = 0
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            if parts[0].isdigit():
                added += int(parts[0])
            if parts[1].isdigit():
                removed += int(parts[1])
    return added, removed


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

        key = str(cwd)
        now = time.monotonic()
        cached = self._entries.get(key)
        if cached is not None and now - cached[0] < self.ttl_seconds:
            return cached[1]
        stat = self._compute(Path(cwd))
        self._entries[key] = (now, stat)
        return stat

    def invalidate(self, cwd: Path | None = None) -> None:
        """Drop cached entries (all, or just ``cwd``)."""

        if cwd is None:
            self._entries.clear()
        else:
            self._entries.pop(str(cwd), None)

    def _compute(self, cwd: Path) -> DiffStat:
        """Shell out to git and fold the results into a :class:`DiffStat`."""

        status = _run_git(cwd, "status", "--porcelain", "--branch")
        if status is None or status.returncode != 0:
            return DiffStat(branch=None)

        branch: str | None = None
        entries: list[str] = []
        for line in status.stdout.splitlines():
            if line.startswith("## "):
                branch = _parse_branch(line)
            elif line:
                entries.append(line)

        added = removed = 0
        for diff_args in (("diff", "--numstat"), ("diff", "--numstat", "--cached")):
            res = _run_git(cwd, *diff_args)
            if res is not None and res.returncode == 0:
                a, r = _sum_numstat(res.stdout)
                added += a
                removed += r

        # Untracked files never appear in ``git diff``; count them read-only via
        # a --no-index compare against /dev/null (returns 1 when they differ).
        for line in entries:
            if line.startswith("?? "):
                path = line[3:]
                res = _run_git(
                    cwd, "diff", "--numstat", "--no-index", "--", "/dev/null", path
                )
                if res is not None:
                    a, r = _sum_numstat(res.stdout)
                    added += a
                    removed += r

        detail_res = _run_git(cwd, "diff", "--stat")
        detail = detail_res.stdout if detail_res is not None else ""

        return DiffStat(
            branch=branch,
            files_changed=len(entries),
            added_lines=added,
            removed_lines=removed,
            dirty=bool(entries),
            detail=detail,
        )
