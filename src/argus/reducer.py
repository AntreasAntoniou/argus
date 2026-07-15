"""Pure-ish state reducer — events fold into the session state machine.

STUB — precise typed contract only. Implementers: implement the transition
table from ``DESIGN.md`` §State machine::

    starting -> thinking <-> tool:<name> -> blocked(question) -> ... -> done | dead

Mapping guidance (source hook -> target status):
    - ``SessionStart``           -> STARTING
    - ``UserPromptSubmit``       -> THINKING
    - ``PreToolUse``             -> TOOL   (set ``tool_name``/``last_tool``)
    - ``PostToolUse``            -> THINKING (clear ``tool_name``, keep ``last_tool``)
    - ``Notification``           -> BLOCKED (extract the question)
    - ``Stop`` / ``SubagentStop``-> IDLE   (turn finished, awaiting next prompt)
    - ``SessionEnd``             -> DONE
    - transcript/tmux liveness   -> may force DEAD (see helpers below)

:func:`reduce` must be pure: it returns a NEW or mutated snapshot but must not
perform I/O. Dead-detection helpers are separate because they depend on wall
clock + tmux liveness, which are effects the caller supplies.
"""

from __future__ import annotations

from datetime import datetime

from argus.config import Thresholds
from argus.models import Event, SessionSnapshot


def reduce(snapshot: SessionSnapshot | None, event: Event) -> SessionSnapshot:
    """Advance a session's state machine by one event.

    Args:
        snapshot: The session's current snapshot, or ``None`` for the first
            event of a session (a fresh ``STARTING`` snapshot is created).
        event: The observation to apply.

    Returns:
        The updated :class:`SessionSnapshot`. Always stamps ``updated_at`` from
        ``event.ts`` and refreshes ``cwd`` when the event carries one.

    Implementation contract:
        - ``STARTING``/``THINKING``/``TOOL`` transitions per the mapping above.
        - Entering ``BLOCKED`` sets ``question`` from the notification payload;
          leaving ``BLOCKED`` (next tool/prompt) clears it.
        - ``TOOL`` sets ``tool_name`` and ``last_tool``; non-TOOL clears
          ``tool_name`` but preserves ``last_tool``.
        - Never regress out of a terminal state (``DONE``/``DEAD``) except an
          explicit restart (new ``SessionStart``).
    """

    raise NotImplementedError("Implement the DESIGN.md state-machine transition table")


def extract_question(event: Event) -> str | None:
    """Pull the human-facing question text from a ``Notification`` event.

    Args:
        event: A ``Notification`` (or idle-with-prompt) event.

    Returns:
        The exact prompt string to show on the board (e.g.
        ``"Run db migration? (y/n)"``), or ``None`` if none present.
    """

    raise NotImplementedError("Parse notification payload for the pending question")


def is_dead(
    snapshot: SessionSnapshot,
    *,
    now: datetime,
    thresholds: Thresholds,
    pane_alive: bool,
    last_jsonl_activity: datetime | None,
) -> bool:
    """Decide whether a session should be marked ``DEAD``.

    A session is dead when its tmux pane is gone, OR its JSONL transcript has
    been silent past ``thresholds.jsonl_silent_seconds`` — in both cases only
    while the session is not already ``DONE`` (``DESIGN.md`` §State machine:
    "dead: tmux pane gone or JSONL silent past threshold while state != done").

    Args:
        snapshot: The session under evaluation.
        now: Current wall-clock time (timezone-aware).
        thresholds: Liveness thresholds from config.
        pane_alive: Whether the session's tmux pane still exists.
        last_jsonl_activity: Timestamp of the last JSONL write, if known.

    Returns:
        ``True`` if the session should transition to ``DEAD``.
    """

    raise NotImplementedError("Implement pane-gone / jsonl-silent dead detection")
