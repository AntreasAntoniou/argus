"""Intended-behavior tests for argus.reply (guarded tmux injection).

Integration cases drive the real ``tmux_server`` fixture (conftest); the scripted
pane runs ``cat``, which echoes each submitted line back, so we can put a prompt
"on screen" and then verify the guard. Unit cases monkeypatch the tmux wrappers
to isolate the guard logic and error handling without a server.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from argus.ingest import tmux
from argus.models import SessionSnapshot, SessionStatus
from argus.reply import ReplyOutcome, guarded_send


def _blocked(session_id: str = "argus") -> SessionSnapshot:
    # session_id doubles as the tmux session name the pane is resolved by; the
    # fixture names its scripted tmux session "argus".
    return SessionSnapshot(session_id=session_id, machine="mac",
                           status=SessionStatus.BLOCKED,
                           question="Run db migration? (y/n)",
                           cwd="/home/dev/example")


def _prime_pane(socket: str, prompt: str) -> str:
    """Put ``prompt`` on the scripted pane's screen and return its pane id."""

    panes = [p for p in tmux.list_panes(socket=socket) if p.session_name == "argus"]
    assert panes, "fixture should expose the scripted 'argus' session"
    pane_id = panes[0].pane_id
    tmux.send_keys(pane_id, prompt, socket=socket, enter=True)
    for _ in range(50):
        if prompt in tmux.capture_pane(pane_id, socket=socket):
            break
        time.sleep(0.02)
    assert prompt in tmux.capture_pane(pane_id, socket=socket)
    return pane_id


# --- integration: real tmux server -----------------------------------------

def test_guarded_send_sends_when_prompt_still_live(tmux_server: str) -> None:
    prompt = "Run db migration? (y/n)"
    _prime_pane(tmux_server, prompt)

    res = guarded_send(_blocked(), "y", prompt, socket=tmux_server)

    assert res.outcome is ReplyOutcome.SENT
    assert res.observed_prompt == prompt


def test_guarded_send_refuses_when_prompt_stale(tmux_server: str) -> None:
    # The pane shows one valid prompt; the caller believes a *different* prompt
    # is live. Must refuse and hand back what is actually on screen.
    on_screen = "Run db migration? (y/n)"
    pane_id = _prime_pane(tmux_server, on_screen)
    marker = "STALE_MARKER_SHOULD_NOT_APPEAR"

    res = guarded_send(_blocked(), marker, "Overwrite config.py? (y/n)",
                       socket=tmux_server)

    assert res.outcome is ReplyOutcome.STALE
    assert res.observed_prompt == on_screen
    assert res.observed_prompt != "Overwrite config.py? (y/n)"
    # Never blind-inject: the marker reply must not have reached the pane.
    time.sleep(0.05)
    assert marker not in tmux.capture_pane(pane_id, socket=tmux_server)


def test_guarded_send_no_pane_when_session_absent(tmux_server: str) -> None:
    res = guarded_send(_blocked("ghost-session-not-in-tmux"), "y", "anything",
                       socket=tmux_server)

    assert res.outcome is ReplyOutcome.NO_PANE
    assert res.observed_prompt is None


# --- unit: monkeypatched tmux wrappers --------------------------------------

def test_guarded_send_stale_when_no_prompt_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pane = tmux.Pane(pane_id="%7", session_name="argus", window_index=0,
                     title="cat")
    monkeypatch.setattr(tmux, "list_panes", lambda *, socket=None: [pane])
    monkeypatch.setattr(tmux, "capture_pane",
                        lambda pane_id, *, socket=None, lines=200: "still working\n")
    sent: list[str] = []
    monkeypatch.setattr(tmux, "send_keys",
                        lambda *a, **k: sent.append(a[1]))

    res = guarded_send(_blocked(), "y", "Run db migration? (y/n)")

    assert res.outcome is ReplyOutcome.STALE
    assert res.observed_prompt is None
    assert sent == []  # refused, nothing injected


def test_guarded_send_error_when_tmux_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(*, socket: str | None = None) -> list[tmux.Pane]:
        raise FileNotFoundError("tmux")

    monkeypatch.setattr(tmux, "list_panes", _boom)

    res = guarded_send(_blocked(), "y", "Run db migration? (y/n)")

    assert res.outcome is ReplyOutcome.ERROR
    assert res.observed_prompt is None
    assert "tmux" in res.detail


def test_guarded_send_error_when_send_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prompt = "Run db migration? (y/n)"
    pane = tmux.Pane(pane_id="%7", session_name="argus", window_index=0,
                     title="cat")
    monkeypatch.setattr(tmux, "list_panes", lambda *, socket=None: [pane])
    monkeypatch.setattr(tmux, "capture_pane",
                        lambda pane_id, *, socket=None, lines=200: prompt + "\n")

    def _fail(*a: object, **k: object) -> None:
        raise subprocess.CalledProcessError(1, "tmux send-keys")

    monkeypatch.setattr(tmux, "send_keys", _fail)

    res = guarded_send(_blocked(), "y", prompt)

    assert res.outcome is ReplyOutcome.ERROR
    assert res.observed_prompt == prompt
