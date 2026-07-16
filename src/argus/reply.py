"""Guarded injection — answer a blocked agent from the board, safely.

Implements ``DESIGN.md`` decision #5 (Guarded injection). Before sending
keystrokes, re-capture the pane and verify the queued prompt is STILL on screen;
if it changed, refuse and signal the caller to refresh the card — never
blind-inject.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from enum import StrEnum

from argus.ingest import tmux
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

    try:
        pane = _resolve_pane(session, socket=socket)
    except FileNotFoundError as exc:  # tmux binary absent
        return Result(ReplyOutcome.ERROR, detail=f"tmux unavailable: {exc}")
    except OSError as exc:  # pragma: no cover - defensive
        return Result(ReplyOutcome.ERROR, detail=f"tmux error: {exc}")

    if pane is None:
        return Result(
            ReplyOutcome.NO_PANE,
            detail=f"no live tmux pane for session {session.session_id!r}",
        )

    try:
        pane_text = tmux.capture_pane(pane.pane_id, socket=socket)
    except subprocess.CalledProcessError:
        # The pane vanished between resolution and capture.
        return Result(
            ReplyOutcome.NO_PANE,
            detail=f"pane {pane.pane_id} vanished before capture",
        )

    observed = tmux.detect_prompt(pane_text)
    if observed != expected_prompt:
        # Prompt changed / gone since the board captured it — refuse and hand
        # back what is actually on screen so the UI can refresh the card.
        return Result(
            ReplyOutcome.STALE,
            detail="prompt changed since capture; refusing to inject",
            observed_prompt=observed,
        )

    try:
        tmux.send_keys(pane.pane_id, text, socket=socket)
    except (subprocess.CalledProcessError, OSError) as exc:
        return Result(
            ReplyOutcome.ERROR,
            detail=f"send-keys failed: {exc}",
            observed_prompt=observed,
        )

    return Result(ReplyOutcome.SENT, observed_prompt=observed)


def _resolve_pane(
    session: SessionSnapshot, *, socket: str | None
) -> tmux.Pane | None:
    """Find the tmux pane hosting ``session``, or ``None`` if it is gone.

    The board carries no pane id on :class:`SessionSnapshot`, so the pane is
    resolved by identity: a pane whose owning tmux ``session_name`` equals the
    Claude ``session_id``, falling back to a pane whose title carries the id.
    """

    panes = tmux.list_panes(socket=socket)
    for pane in panes:
        if pane.session_name == session.session_id:
            return pane
    for pane in panes:
        if session.session_id in pane.title:
            return pane
    return None
