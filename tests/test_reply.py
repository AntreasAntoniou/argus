"""Intended-behavior tests for argus.reply (stub → xfail until implemented)."""

from __future__ import annotations

import pytest

from argus.models import SessionSnapshot, SessionStatus
from argus.reply import ReplyOutcome, guarded_send


def _blocked() -> SessionSnapshot:
    return SessionSnapshot(session_id="s1", machine="mac",
                           status=SessionStatus.BLOCKED,
                           question="Run db migration? (y/n)",
                           cwd="/home/dev/example")


@pytest.mark.xfail(reason="stub", strict=False)
def test_guarded_send_sends_when_prompt_still_live(tmux_server: str) -> None:
    res = guarded_send(_blocked(), "y", "Run db migration? (y/n)",
                       socket=tmux_server)
    assert res.outcome is ReplyOutcome.SENT


@pytest.mark.xfail(reason="stub", strict=False)
def test_guarded_send_refuses_when_prompt_stale(tmux_server: str) -> None:
    # Expected prompt is NOT what the pane currently shows → must refuse.
    res = guarded_send(_blocked(), "y", "A prompt that is no longer on screen",
                       socket=tmux_server)
    assert res.outcome is ReplyOutcome.STALE
    assert res.observed_prompt != "A prompt that is no longer on screen"
