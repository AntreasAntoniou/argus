"""Shared pytest fixtures for the Argus suite.

Provides paths to the synthetic JSONL fixtures and a real, scripted tmux server
on a throwaway socket for poller/reply integration tests. The tmux fixture skips
cleanly when ``tmux`` is not installed, so the suite stays green on any host.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def fixtures_dir() -> Path:
    """Return the directory holding the synthetic JSONL fixtures."""

    return FIXTURES


@pytest.fixture
def clean_transcript() -> Path:
    """Path to the clean (start→tools→done) synthetic transcript."""

    return FIXTURES / "clean_session.jsonl"


@pytest.fixture
def blocked_transcript() -> Path:
    """Path to the blocked (notification/permission prompt) synthetic transcript."""

    return FIXTURES / "blocked_session.jsonl"


@pytest.fixture
def tool_heavy_transcript() -> Path:
    """Path to the tool-dense synthetic transcript."""

    return FIXTURES / "tool_heavy_session.jsonl"


@pytest.fixture
def tmux_server() -> Iterator[str]:
    """Spin up a real, isolated tmux server on a per-pid socket.

    Yields the socket name (``-L`` value) with one detached session named
    ``argus`` running a long-lived ``cat`` so the pane stays alive for capture /
    send-keys / liveness tests. Skips the test when ``tmux`` is absent.

    Yields:
        The tmux socket name to pass as ``socket=`` to
        :mod:`argus.ingest.tmux` helpers.
    """

    if shutil.which("tmux") is None:
        pytest.skip("tmux not installed")

    socket = f"argus-test-{os.getpid()}"
    subprocess.run(
        ["tmux", "-L", socket, "new-session", "-d", "-s", "argus", "cat"],
        check=True,
    )
    try:
        yield socket
    finally:
        subprocess.run(
            ["tmux", "-L", socket, "kill-server"],
            check=False,
            stderr=subprocess.DEVNULL,
        )
