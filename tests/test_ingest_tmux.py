"""Intended-behavior tests for argus.ingest.tmux.

The tmux_server fixture (conftest) provides a real scripted server; these tests
exercise the wrappers against it and skip cleanly when tmux is absent. The
``detect_prompt`` heuristic cases are pure and run without a tmux server.
"""

from __future__ import annotations

import subprocess
import time

import pytest

from argus.ingest.tmux import (
    Pane,
    capture_pane,
    detect_prompt,
    is_pane_alive,
    list_panes,
    send_keys,
)


def test_list_panes_sees_scripted_session(tmux_server: str) -> None:
    panes = list_panes(socket=tmux_server)
    assert panes
    argus_panes = [p for p in panes if p.session_name == "argus"]
    assert argus_panes
    pane = argus_panes[0]
    assert isinstance(pane, Pane)
    assert pane.pane_id.startswith("%")
    assert isinstance(pane.window_index, int)
    assert pane.pid is None or isinstance(pane.pid, int)


def test_list_panes_missing_binary_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*args: object, **kwargs: object) -> None:
        raise FileNotFoundError("tmux")

    monkeypatch.setattr(subprocess, "run", _boom)
    with pytest.raises(FileNotFoundError):
        list_panes(socket="does-not-matter")


def test_capture_and_liveness(tmux_server: str) -> None:
    panes = list_panes(socket=tmux_server)
    pane_id = panes[0].pane_id
    assert is_pane_alive(pane_id, socket=tmux_server)
    assert not is_pane_alive("%99999", socket=tmux_server)
    text = capture_pane(pane_id, socket=tmux_server)
    assert isinstance(text, str)


def test_capture_missing_pane_raises(tmux_server: str) -> None:
    with pytest.raises(subprocess.CalledProcessError):
        capture_pane("%99999", socket=tmux_server)


def test_send_keys_reaches_pane(tmux_server: str) -> None:
    # The scripted pane runs `cat`, which echoes each submitted line back.
    panes = list_panes(socket=tmux_server)
    pane_id = panes[0].pane_id
    send_keys(pane_id, "argus-marker", socket=tmux_server, enter=True)
    for _ in range(50):
        if "argus-marker" in capture_pane(pane_id, socket=tmux_server):
            break
        time.sleep(0.02)
    assert "argus-marker" in capture_pane(pane_id, socket=tmux_server)


def test_detect_prompt_finds_yes_no_question() -> None:
    assert detect_prompt("... proceed? (y/n)") is not None
    assert detect_prompt("still working on the refactor") is None


def test_detect_prompt_positive_permission_box() -> None:
    box = (
        "I'll run the migration now.\n"
        "Do you want to proceed?\n"
        "❯ 1. Yes\n"
        "  2. No, tell me what to change\n"
    )
    detected = detect_prompt(box)
    assert detected is not None
    assert "proceed" in detected.lower()


def test_detect_prompt_negative_working_buffer() -> None:
    buffer = (
        "Reading src/argus/reducer.py\n"
        "Running the test suite...\n"
        "42 passed in 3.10s\n"
        "Editing timeline.py to collapse tool rows\n"
    )
    assert detect_prompt(buffer) is None
