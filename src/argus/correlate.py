"""Correlate live tmux panes to Claude session ids.

The missing link between the tmux poller and the reducer: Claude Code session
ids are UUIDs, but tmux panes are named by shell/command — the two namespaces
never intersect, so the daemon could never tell which pane runs which session
(``DESIGN.md`` liveness / ``is_dead``). This module bridges them exactly, the
way the running processes actually reveal it:

1. **Which panes are agents?** Claude Code renames its process to its version
   string (e.g. ``2.1.211``), so a pane whose ``pane_current_command`` looks
   like a version — or is a bare ``node`` / ``claude`` — is running an agent.
2. **Which session?** The claude process's own argv carries it:
   ``--session-id <uuid>`` for a top-level session, ``--parent-session-id
   <uuid>`` for its team subagents (which share the parent's board entry). We
   find the pane's claude process by walking the pane's process subtree from a
   single ``ps`` snapshot — no per-pane calls.
3. **Fallback.** A pane whose claude was started without an id in argv (a fresh
   interactive session) is matched by the freshest transcript under its cwd:
   ``~/.claude/projects/<cwd-slug>/<session-id>.jsonl``, where the slug is the
   cwd with ``/`` and ``.`` turned into ``-``.

The result — ``{session_id: PaneSession}`` — is exactly the "these sessions have
a live pane" evidence :func:`argus.reducer.is_dead` needs, and the pane id the
blocked-detector captures to read an on-screen prompt.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from argus.ingest.tmux import Pane

# A pane running an agent: Claude Code sets its process comm to its version
# (``2.1.211``); bare ``node``/``claude`` cover launcher/older variants.
_VERSION_COMM = re.compile(r"^\d+\.\d+\.\d+")
_AGENT_COMMS = frozenset({"node", "claude"})

# Session id as it appears in a live claude process's argv. ``--session-id`` is
# the session itself; ``--parent-session-id`` is a team subagent pointing at its
# parent (same board entry). A UUID is 8-4-4-4-12 hex.
_SESSION_ARG = re.compile(
    r"--(?:session-id|parent-session-id)\s+([0-9a-fA-F-]{36})"
)


@dataclass(frozen=True, slots=True)
class Proc:
    """One row of a ``ps`` process snapshot.

    Attributes:
        pid: Process id.
        ppid: Parent process id (for subtree walks).
        command: Full command line (argv), where session flags live.
    """

    pid: int
    ppid: int
    command: str


@dataclass(frozen=True, slots=True)
class PaneSession:
    """A live tmux pane resolved to the Claude session it is running.

    Attributes:
        pane_id: tmux pane id (e.g. ``%3``) — the target for prompt capture.
        session_id: The Claude session UUID driving that pane.
        cwd: The working directory, when known (``None`` if unresolved).
    """

    pane_id: str
    session_id: str
    cwd: str | None = None


def is_agent_pane(pane: Pane) -> bool:
    """Return whether ``pane``'s foreground process looks like a Claude agent."""

    title = pane.title or ""
    return bool(_VERSION_COMM.match(title)) or title in _AGENT_COMMS


def session_id_from_command(command: str) -> str | None:
    """Extract the session UUID from a claude process's argv, or ``None``."""

    match = _SESSION_ARG.search(command)
    return match.group(1) if match else None


def cwd_to_project_slug(cwd: str) -> str:
    """Encode a cwd the way Claude Code names its ``~/.claude/projects`` dir.

    ``/Users/antreas/.claude`` → ``-Users-antreas--claude`` (leading slash and
    every ``/`` and ``.`` become ``-``).
    """

    return "-" + cwd.strip("/").replace("/", "-").replace(".", "-")


# -- process snapshot ---------------------------------------------------------


def list_procs(
    *, _runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
) -> dict[int, Proc]:
    """Snapshot the process table as ``{pid: Proc}`` in one ``ps`` call."""

    try:
        proc = _runner(
            ["ps", "-axo", "pid=,ppid=,command="],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {}
    procs: dict[int, Proc] = {}
    for line in proc.stdout.splitlines():
        match = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if not match:
            continue
        pid, ppid = int(match.group(1)), int(match.group(2))
        procs[pid] = Proc(pid=pid, ppid=ppid, command=match.group(3))
    return procs


def _children_index(procs: dict[int, Proc]) -> dict[int, list[int]]:
    """Build ``{ppid: [child pid, ...]}`` for subtree walks."""

    children: dict[int, list[int]] = {}
    for proc in procs.values():
        children.setdefault(proc.ppid, []).append(proc.pid)
    return children


def _subtree(root: int, children: dict[int, list[int]]) -> list[int]:
    """Return ``root`` plus every descendant pid (depth-first, cycle-safe)."""

    out: list[int] = []
    seen: set[int] = set()
    stack = [root]
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        stack.extend(children.get(pid, ()))
    return out


# -- lsof cwd fallback --------------------------------------------------------


def parse_lsof_cwds(output: str) -> dict[int, str]:
    """Parse ``lsof -Fpn`` field output into ``{pid: cwd}``."""

    cwds: dict[int, str] = {}
    pid: int | None = None
    for line in output.splitlines():
        if not line:
            continue
        tag, value = line[0], line[1:]
        if tag == "p":
            try:
                pid = int(value)
            except ValueError:
                pid = None
        elif tag == "n" and pid is not None:
            cwds[pid] = value
    return cwds


def resolve_cwds(
    pids: Iterable[int],
    *,
    _runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> dict[int, str]:
    """Resolve each pid's cwd via one batched ``lsof`` call (``{pid: cwd}``)."""

    pid_list = [str(p) for p in pids]
    if not pid_list:
        return {}
    try:
        proc = _runner(
            ["lsof", "-a", "-d", "cwd", "-Fpn", "-p", ",".join(pid_list)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        return {}
    return parse_lsof_cwds(proc.stdout)


def freshest_session_for_cwd(
    cwd: str,
    *,
    projects_root: Path,
    now: datetime | None = None,
    max_age_seconds: float | None = None,
) -> str | None:
    """Return the session id of the newest transcript under ``cwd``'s project dir.

    Returns the freshest ``*.jsonl`` filename stem, or ``None`` if the project
    dir is absent/empty or (when ``now`` + ``max_age_seconds`` are given) only
    holds transcripts older than the staleness bound.
    """

    directory = Path(projects_root) / cwd_to_project_slug(cwd)
    if not directory.is_dir():
        return None

    best: Path | None = None
    best_mtime = -1.0
    for path in directory.glob("*.jsonl"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime:
            best_mtime, best = mtime, path

    if best is None:
        return None
    if max_age_seconds is not None and now is not None:
        if best_mtime < (now - timedelta(seconds=max_age_seconds)).timestamp():
            return None
    return best.stem


# -- correlation --------------------------------------------------------------


def correlate(
    panes: Iterable[Pane],
    *,
    procs: dict[int, Proc] | None = None,
    projects_root: Path | None = None,
    now: datetime | None = None,
    max_age_seconds: float | None = None,
    cwd_resolver: Callable[[list[int]], dict[int, str]] = resolve_cwds,
) -> dict[str, PaneSession]:
    """Map every agent pane to the session it is running.

    Primary path: walk each agent pane's process subtree and read the session id
    from the claude process's argv (exact, disambiguates concurrent sessions in
    the same directory). Fallback (only when ``projects_root`` is given): a pane
    whose argv carries no id is matched by the freshest transcript in its cwd.

    Args:
        panes: Panes from :func:`argus.ingest.tmux.list_panes`.
        procs: Process snapshot; defaults to a fresh :func:`list_procs` call.
        projects_root: ``~/.claude/projects`` — enables the cwd fallback and cwd
            enrichment on results. ``None`` disables the fallback.
        now / max_age_seconds: Optional staleness bound for the fallback.
        cwd_resolver: Injectable pid→cwd resolver for the fallback.

    Returns:
        ``{session_id: PaneSession}``. When several panes resolve to one session
        (a team's subagent panes), the last wins — all prove the session is live.
    """

    if procs is None:
        procs = list_procs()
    children = _children_index(procs)
    agents = [p for p in panes if p.pid is not None and is_agent_pane(p)]

    result: dict[str, PaneSession] = {}
    unresolved: list[Pane] = []

    # Primary: exact session id from the pane's claude process argv.
    for pane in agents:
        session_id: str | None = None
        for pid in _subtree(pane.pid, children):  # type: ignore[arg-type]
            proc = procs.get(pid)
            if proc is None:
                continue
            session_id = session_id_from_command(proc.command)
            if session_id:
                break
        if session_id:
            result[session_id] = PaneSession(
                pane_id=pane.pane_id, session_id=session_id
            )
        elif projects_root is not None:
            unresolved.append(pane)

    # Fallback: freshest transcript under the pane's cwd (needs projects_root).
    if unresolved and projects_root is not None:
        cwds = cwd_resolver([p.pid for p in unresolved if p.pid is not None])
        for pane in unresolved:
            cwd = cwds.get(pane.pid) if pane.pid is not None else None
            if not cwd:
                continue
            session_id = freshest_session_for_cwd(
                cwd,
                projects_root=projects_root,
                now=now,
                max_age_seconds=max_age_seconds,
            )
            if session_id and session_id not in result:
                result[session_id] = PaneSession(
                    pane_id=pane.pane_id, session_id=session_id, cwd=cwd
                )
    return result
