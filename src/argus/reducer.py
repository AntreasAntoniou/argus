"""Pure-ish state reducer — events fold into the session state machine.

Implements the transition table from ``DESIGN.md`` §State machine::

    starting -> thinking <-> tool:<name> -> blocked(question) -> ... -> done | dead

Mapping (source hook -> target status):
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

from datetime import datetime, timedelta

from argus.config import Thresholds
from argus.models import Event, HookEvent, SessionSnapshot, SessionStatus

# Terminal states never regress except on an explicit restart (SessionStart).
_TERMINAL = (SessionStatus.DONE, SessionStatus.DEAD)

# Notification payload keys, most-specific first, that may carry the prompt.
_QUESTION_KEYS = ("notification", "message", "prompt", "question", "body")


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

    hook = event.hook_event_name
    is_restart = hook == HookEvent.SESSION_START

    if snapshot is None:
        snapshot = SessionSnapshot(
            session_id=event.session_id,
            machine=event.machine,
            status=SessionStatus.STARTING,
        )

    # Always refresh liveness metadata (updated_at, cwd) even when a terminal
    # state pins the status.
    snapshot.updated_at = event.ts
    if event.cwd is not None:
        snapshot.cwd = event.cwd

    # A terminal session only advances on an explicit restart.
    if snapshot.status in _TERMINAL and not is_restart:
        return snapshot

    if is_restart:
        snapshot.status = SessionStatus.STARTING
        snapshot.tool_name = None
        snapshot.question = None
    elif hook == HookEvent.USER_PROMPT_SUBMIT:
        snapshot.status = SessionStatus.THINKING
        snapshot.tool_name = None
        snapshot.question = None
    elif hook == HookEvent.PRE_TOOL_USE:
        snapshot.status = SessionStatus.TOOL
        snapshot.tool_name = event.tool_name
        if event.tool_name is not None:
            snapshot.last_tool = event.tool_name
        snapshot.question = None
    elif hook == HookEvent.POST_TOOL_USE:
        snapshot.status = SessionStatus.THINKING
        if event.tool_name is not None:
            snapshot.last_tool = event.tool_name
        snapshot.tool_name = None
        snapshot.question = None
    elif hook == HookEvent.NOTIFICATION:
        snapshot.status = SessionStatus.BLOCKED
        snapshot.tool_name = None
        snapshot.question = extract_question(event)
    elif hook in (HookEvent.STOP, HookEvent.SUBAGENT_STOP):
        snapshot.status = SessionStatus.IDLE
        snapshot.tool_name = None
        snapshot.question = None
    elif hook == HookEvent.SESSION_END:
        snapshot.status = SessionStatus.DONE
        snapshot.tool_name = None
        snapshot.question = None
    elif snapshot.status is not SessionStatus.TOOL:
        # Unknown / synthetic (non-hook) events (e.g. "transcript", "tmux.dead")
        # only refresh metadata above and drive no transition, but we still
        # enforce the tool_name-iff-TOOL invariant defensively.
        snapshot.tool_name = None

    return snapshot


def extract_question(event: Event) -> str | None:
    """Pull the human-facing question text from a ``Notification`` event.

    Args:
        event: A ``Notification`` (or idle-with-prompt) event.

    Returns:
        The exact prompt string to show on the board (e.g.
        ``"Run db migration? (y/n)"``), or ``None`` if none present.
    """

    raw = event.raw or {}
    for key in _QUESTION_KEYS:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return None


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

    if snapshot.status is SessionStatus.DONE:
        return False

    if not pane_alive:
        return True

    if last_jsonl_activity is not None:
        silent_for = now - last_jsonl_activity
        if silent_for > timedelta(seconds=thresholds.jsonl_silent_seconds):
            return True

    return False
