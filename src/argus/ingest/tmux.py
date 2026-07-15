"""tmux poller — subprocess wrappers for liveness, capture, and prompt detection.

STUB — precise typed contract only. Implementers: shell out to ``tmux`` via
``subprocess`` to enumerate panes, capture their contents, decide liveness, and
detect an on-screen prompt. This is the liveness + death-detection + guarded-
reply channel of ``DESIGN.md`` decision #1/#5. Keep every call parameterised by
tmux socket name so tests can run against a throwaway ``tmux -L argus-test-*``
server.
"""

from __future__ import annotations

import subprocess  # noqa: F401  (used by implementers of the wrappers below)
from dataclasses import dataclass


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


def list_panes(*, socket: str | None = None) -> list[Pane]:
    """Enumerate all tmux panes on the given server.

    Args:
        socket: tmux socket name (``-L``); ``None`` uses the default server.

    Returns:
        Every live :class:`Pane`. Empty list if the server has no sessions.

    Raises:
        FileNotFoundError: If the ``tmux`` binary is not on ``PATH``.
    """

    raise NotImplementedError("tmux list-panes -a -F <format>, parse into Pane list")


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

    raise NotImplementedError("tmux capture-pane -p -t <pane> -S -<lines>")


def is_pane_alive(pane_id: str, *, socket: str | None = None) -> bool:
    """Return whether a pane still exists on the server.

    Feeds :func:`argus.reducer.is_dead` (pane-gone -> dead).
    """

    raise NotImplementedError("Check pane_id against list_panes()")


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

    raise NotImplementedError("Regex/heuristic detect a pending prompt in pane text")


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

    raise NotImplementedError("tmux send-keys -t <pane> -l <text> [Enter]")
