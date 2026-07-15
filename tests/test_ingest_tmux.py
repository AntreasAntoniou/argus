"""Intended-behavior tests for argus.ingest.tmux (stub → xfail).

The tmux_server fixture (conftest) provides a real scripted server; these tests
exercise the wrappers against it and skip cleanly when tmux is absent.
"""

from __future__ import annotations

import pytest

from argus.ingest.tmux import capture_pane, detect_prompt, is_pane_alive, list_panes


@pytest.mark.xfail(reason="stub", strict=False)
def test_list_panes_sees_scripted_session(tmux_server: str) -> None:
    panes = list_panes(socket=tmux_server)
    assert any(p.session_name == "argus" for p in panes)


@pytest.mark.xfail(reason="stub", strict=False)
def test_capture_and_liveness(tmux_server: str) -> None:
    panes = list_panes(socket=tmux_server)
    pane_id = panes[0].pane_id
    assert is_pane_alive(pane_id, socket=tmux_server)
    text = capture_pane(pane_id, socket=tmux_server)
    assert isinstance(text, str)


@pytest.mark.xfail(reason="stub", strict=False)
def test_detect_prompt_finds_yes_no_question() -> None:
    assert detect_prompt("... proceed? (y/n)") is not None
    assert detect_prompt("still working on the refactor") is None
