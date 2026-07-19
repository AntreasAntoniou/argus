"""tmux poller — subprocess wrappers for liveness, capture, and prompt detection.

STUB — precise typed contract only. Implementers: shell out to ``tmux`` via
``subprocess`` to enumerate panes, capture their contents, decide liveness, and
detect an on-screen prompt. This is the liveness + death-detection + guarded-
reply channel of ``DESIGN.md`` decision #1/#5. Keep every call parameterised by
tmux socket name so tests can run against a throwaway ``tmux -L argus-test-*``
server.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

# Field separator for the ``list-panes`` format string: a control char that will
# not occur inside a session name, command, or pane title.
_SEP = "\x1f"

_LIST_FORMAT = _SEP.join(
    (
        "#{pane_id}",
        "#{session_name}",
        "#{window_index}",
        "#{pane_current_command}",
        "#{pane_pid}",
    )
)

# Heuristic markers of an on-screen prompt awaiting the human. Kept deliberately
# narrow so an agent's ordinary working output does not read as a question:
#   - an explicit yes/no affordance ``(y/n)`` / ``[y/n]`` / ``(yes/no)``
#   - Claude Code's permission box lead-in ``Do you want to ...``
#   - a trailing ``proceed?`` / ``continue?`` confirmation
#   - a ``Press Enter to continue`` gate
_PROMPT_PATTERNS = (
    re.compile(r"[(\[]\s*y\s*/\s*n\s*[)\]]", re.IGNORECASE),
    re.compile(r"\(\s*yes\s*/\s*no\s*\)", re.IGNORECASE),
    re.compile(r"\bdo you want to\b", re.IGNORECASE),
    re.compile(r"\b(?:proceed|continue|overwrite|allow)\s*\?", re.IGNORECASE),
    re.compile(r"press\s+(?:enter|return)\s+to\s+continue", re.IGNORECASE),
)


@dataclass(frozen=True, slots=True)
class Pane:
    """One tmux pane, as reported by ``tmux list-panes``.

    Attributes:
        pane_id: tmux pane id (e.g. ``%3``).
        session_name: Owning tmux session name.
        window_index: Window index within the session.
        title: Pane title / current command.
        pid: PID of the pane's foreground process, if resolvable.
    """

    pane_id: str
    session_name: str
    window_index: int
    title: str
    pid: int | None = None


def _tmux(*args: str, socket: str | None) -> list[str]:
    """Build a ``tmux`` argv, threading ``-L <socket>`` when given."""

    cmd = ["tmux"]
    if socket is not None:
        cmd += ["-L", socket]
    cmd += list(args)
    return cmd


def list_panes(*, socket: str | None = None) -> list[Pane]:
    """Enumerate all tmux panes on the given server.

    Args:
        socket: tmux socket name (``-L``); ``None`` uses the default server.

    Returns:
        Every live :class:`Pane`. Empty list if the server has no sessions.

    Raises:
        FileNotFoundError: If the ``tmux`` binary is not on ``PATH``.
    """

    proc = subprocess.run(
        _tmux("list-panes", "-a", "-F", _LIST_FORMAT, socket=socket),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        # No server / no sessions is not an error for us — just nothing to list.
        return []

    panes: list[Pane] = []
    for line in proc.stdout.splitlines():
        if not line:
            continue
        pane_id, session_name, window_index, title, pid = line.split(_SEP)
        panes.append(
            Pane(
                pane_id=pane_id,
                session_name=session_name,
                window_index=int(window_index),
                title=title,
                pid=int(pid) if pid else None,
            )
        )
    return panes


def capture_pane(pane_id: str, *, socket: str | None = None, lines: int = 200) -> str:
    """Capture the visible (and recent scrollback) text of a pane.

    Args:
        pane_id: Target pane id.
        socket: tmux socket name (``-L``).
        lines: How many trailing lines of scrollback to include.

    Returns:
        The captured pane text.

    Raises:
        subprocess.CalledProcessError: If the pane does not exist.
    """

    proc = subprocess.run(
        _tmux(
            "capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}", socket=socket
        ),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def is_pane_alive(pane_id: str, *, socket: str | None = None) -> bool:
    """Return whether a pane still exists on the server.

    Feeds :func:`argus.reducer.is_dead` (pane-gone -> dead).
    """

    return any(p.pane_id == pane_id for p in list_panes(socket=socket))


def detect_prompt(pane_text: str) -> str | None:
    """Detect an on-screen prompt awaiting the human in captured pane text.

    Recognises Claude Code permission prompts / ``(y/n)`` questions / idle input
    boxes. Used both for idle-with-prompt BLOCKED detection and as the
    ``expected_prompt`` check in :func:`argus.reply.guarded_send`.

    Args:
        pane_text: Output of :func:`capture_pane`.

    Returns:
        The detected prompt/question string, or ``None`` if the pane shows no
        prompt (agent still working).
    """

    for line in reversed(pane_text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        if any(pat.search(stripped) for pat in _PROMPT_PATTERNS):
            return stripped
    return None


def send_keys(
    pane_id: str,
    text: str,
    *,
    socket: str | None = None,
    enter: bool = True,
) -> None:
    """Send literal keystrokes to a pane (the injection primitive).

    Low-level; callers should prefer :func:`argus.reply.guarded_send`, which
    verifies the prompt is still live before calling this.

    Args:
        pane_id: Target pane id.
        text: Literal text to type.
        socket: tmux socket name (``-L``).
        enter: Whether to append a trailing ``Enter``.
    """

    # ``-l`` sends the text literally so key names inside it are not interpreted.
    subprocess.run(
        _tmux("send-keys", "-t", pane_id, "-l", text, socket=socket),
        check=True,
    )
    if enter:
        # A separate, non-literal call so ``Enter`` resolves to the key.
        subprocess.run(
            _tmux("send-keys", "-t", pane_id, "Enter", socket=socket),
            check=True,
        )


def list_clients(*, socket: str | None = None) -> list[str]:
    """Return the tty of every tmux client currently attached to the server."""

    proc = subprocess.run(
        _tmux("list-clients", "-F", "#{client_tty}", socket=socket),
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        return []
    return [line for line in proc.stdout.splitlines() if line]


def focus_pane(pane_id: str, *, socket: str | None = None) -> bool:
    """Bring a pane to the foreground for every attached tmux client.

    Resolves the pane's window, switches each attached client to it, and selects
    the pane — so "jump to this agent" from the board or TUI actually lands the
    user's terminal on the right pane. Returns ``False`` if the pane no longer
    exists; still selects the pane (for the next attach) when no client is live.
    """

    win = subprocess.run(
        _tmux(
            "display-message", "-p", "-t", pane_id,
            "#{session_name}:#{window_index}", socket=socket,
        ),
        capture_output=True,
        text=True,
    )
    target = win.stdout.strip()
    if win.returncode != 0 or not target:
        return False

    for client in list_clients(socket=socket):
        subprocess.run(
            _tmux("switch-client", "-c", client, "-t", target, socket=socket),
            check=False,
        )
    subprocess.run(
        _tmux("select-pane", "-t", pane_id, socket=socket), check=False
    )
    return True
