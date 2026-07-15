"""Guarded injection — answer a blocked agent from the board, safely.

STUB — precise typed contract only. Implementers: implement ``DESIGN.md``
decision #5 (Guarded injection). Before sending keystrokes, re-capture the pane
and verify the queued prompt is STILL on screen; if it changed, refuse and
signal the caller to refresh the card — never blind-inject.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from argus.models import SessionSnapshot


class ReplyOutcome(StrEnum):
    """The result of a guarded send attempt."""

    SENT = "sent"
    STALE = "stale"  # prompt changed since capture; refused, refresh the card
    NO_PANE = "no_pane"  # session's pane vanished
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Result:
    """Outcome of :func:`guarded_send`.

    Attributes:
        outcome: The :class:`ReplyOutcome`.
        detail: Human-readable explanation (shown on the card when not SENT).
        observed_prompt: The prompt actually on screen at send time (for STALE,
            this is what to re-render).
    """

    outcome: ReplyOutcome
    detail: str = ""
    observed_prompt: str | None = None


def guarded_send(
    session: SessionSnapshot,
    text: str,
    expected_prompt: str,
    *,
    socket: str | None = None,
) -> Result:
    """Send ``text`` to a session's pane only if ``expected_prompt`` is still live.

    Flow (``DESIGN.md`` decision #5): resolve the session's tmux pane ->
    :func:`argus.ingest.tmux.capture_pane` -> confirm ``expected_prompt`` is
    still present via :func:`argus.ingest.tmux.detect_prompt`; if present,
    :func:`argus.ingest.tmux.send_keys`; if changed/absent, return
    :attr:`ReplyOutcome.STALE` with the freshly observed prompt so the UI
    refreshes instead of blind-injecting.

    Args:
        session: The blocked session to answer.
        text: The reply to type (``"y"`` / ``"n"`` / a typed message).
        expected_prompt: The prompt the board showed when the human answered.
        socket: tmux socket name for the target server.

    Returns:
        A :class:`Result` describing what happened.
    """

    raise NotImplementedError(
        "Capture pane, verify expected_prompt, send-keys or STALE"
    )
