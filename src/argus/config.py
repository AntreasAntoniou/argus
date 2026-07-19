"""Argus configuration — fully implemented, stdlib-only reading.

Config and secrets live in ``~/.argus/`` (never in the repo — the repo is
public). :func:`load_config` reads ``~/.argus/config.toml`` with stdlib
``tomllib``; a missing file yields documented defaults so a fresh machine runs
out of the box. Writing config (used by ``argus install-hooks`` and first-run
scaffolding) is done elsewhere with ``tomli-w``.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

# Root for all runtime state and secrets. Kept out of git by design.
ARGUS_HOME = Path.home() / ".argus"
DEFAULT_CONFIG_PATH = ARGUS_HOME / "config.toml"


class NotifierKind(StrEnum):
    """Supported push-notifier backends (see ``DESIGN.md`` decision #7)."""

    WHATSAPP = "whatsapp"
    NOOP = "noop"


@dataclass(slots=True)
class Thresholds:
    """Timing thresholds for liveness and notification batching.

    Attributes:
        dead_after_seconds: A non-blocked session silent (no JSONL/hook activity)
            for longer than this, with no matched live tmux pane, is marked
            ``dead``. Generous by default so a session you briefly stepped away
            from is not falsely buried; BLOCKED sessions are never aged out.
        jsonl_silent_seconds: Soft "idle" hint window (informational); dead
            detection itself keys off ``dead_after_seconds``.
        poll_interval_seconds: How often the tmux/liveness sweep runs (decoupled
            from the death window so death can be generous while the sweep stays
            responsive).
        notify_batch_seconds: Minimum spacing between non-critical WhatsApp
            digests. Defaults to 300 (≤1 msg / 5 min per decision #7).
        board_window_seconds: How recently a session must have been active to
            appear on the board. Sessions quiet longer than this drop off the
            departures board (the journal keeps them for timelines) — this is
            what stops a multi-week journal from burying today's live fleet under
            hundreds of finished sessions. ``needs_you`` (BLOCKED) sessions are
            always shown regardless of age. Defaults to 1800 (30 min).
    """

    dead_after_seconds: int = 600
    jsonl_silent_seconds: int = 45
    poll_interval_seconds: int = 5
    notify_batch_seconds: int = 300
    board_window_seconds: int = 1800


@dataclass(slots=True)
class NotifierConfig:
    """Push-notifier configuration.

    Attributes:
        kind: Which backend to use. Defaults to ``NOOP`` so a fresh install is
            silent until WhatsApp is explicitly configured.
        whatsapp_command: Shell command template for the WhatsApp backend. The
            token ``{message}`` is substituted with the digest text at send time
            (see :class:`argus.notify.WhatsAppNotifier`).
    """

    kind: NotifierKind = NotifierKind.NOOP
    whatsapp_command: str = 'agent-comms whatsapp send --self --message "{message}"'


@dataclass(slots=True)
class Paths:
    """Filesystem locations Argus reads and writes.

    Attributes:
        claude_projects_root: Root the transcript watcher tails
            (``~/.claude/projects``); its ``**/*.jsonl`` files are the catch-all
            state source.
        journal_path: SQLite event journal (under ``~/.argus``) that lets the
            daemon recover snapshots across restarts.
        settings_path: Claude Code settings file that ``argus install-hooks``
            idempotently merges the async hook set into.
    """

    claude_projects_root: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "projects"
    )
    journal_path: Path = field(default_factory=lambda: ARGUS_HOME / "journal.sqlite3")
    settings_path: Path = field(
        default_factory=lambda: Path.home() / ".claude" / "settings.json"
    )


@dataclass(slots=True)
class ArgusConfig:
    """Top-level Argus configuration.

    Attributes:
        machine: This node's identity in the mesh (defaults to the hostname).
        peers: Static peer list as ``host:port`` strings for full-mesh
            federation (decision #6). Excludes self.
        daemon_port: Port ``argusd`` binds its FastAPI/SSE server to.
        thresholds: Liveness and notification-batching thresholds.
        notifier: Push-notifier configuration.
        paths: Filesystem locations.
    """

    machine: str = field(default_factory=lambda: _hostname())
    peers: list[str] = field(default_factory=list)
    daemon_port: int = 8787
    thresholds: Thresholds = field(default_factory=Thresholds)
    notifier: NotifierConfig = field(default_factory=NotifierConfig)
    paths: Paths = field(default_factory=Paths)


def _hostname() -> str:
    """Return this machine's short hostname (federation tag)."""

    import socket

    return socket.gethostname().split(".")[0]


def _coerce_thresholds(data: dict[str, Any]) -> Thresholds:
    defaults = Thresholds()
    return Thresholds(
        dead_after_seconds=int(
            data.get("dead_after_seconds", defaults.dead_after_seconds)
        ),
        jsonl_silent_seconds=int(
            data.get("jsonl_silent_seconds", defaults.jsonl_silent_seconds)
        ),
        poll_interval_seconds=int(
            data.get("poll_interval_seconds", defaults.poll_interval_seconds)
        ),
        notify_batch_seconds=int(
            data.get("notify_batch_seconds", defaults.notify_batch_seconds)
        ),
        board_window_seconds=int(
            data.get("board_window_seconds", defaults.board_window_seconds)
        ),
    )


def _coerce_notifier(data: dict[str, Any]) -> NotifierConfig:
    defaults = NotifierConfig()
    return NotifierConfig(
        kind=NotifierKind(data.get("kind", defaults.kind.value)),
        whatsapp_command=str(data.get("whatsapp_command", defaults.whatsapp_command)),
    )


def _coerce_paths(data: dict[str, Any]) -> Paths:
    defaults = Paths()
    return Paths(
        claude_projects_root=Path(
            data.get("claude_projects_root", defaults.claude_projects_root)
        ).expanduser(),
        journal_path=Path(
            data.get("journal_path", defaults.journal_path)
        ).expanduser(),
        settings_path=Path(
            data.get("settings_path", defaults.settings_path)
        ).expanduser(),
    )


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> ArgusConfig:
    """Load :class:`ArgusConfig` from a TOML file, falling back to defaults.

    A missing file returns a fully-defaulted config (a fresh machine runs with
    no config at all). Present-but-partial files override only the keys they
    specify. Unknown keys are ignored.

    Args:
        path: Path to the TOML config. Defaults to ``~/.argus/config.toml``.

    Returns:
        A populated :class:`ArgusConfig`.

    Raises:
        tomllib.TOMLDecodeError: If the file exists but is not valid TOML.
    """

    path = Path(path).expanduser()
    if not path.exists():
        return ArgusConfig()

    with path.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)

    defaults = ArgusConfig()
    return ArgusConfig(
        machine=str(data.get("machine", defaults.machine)),
        peers=list(data.get("peers", defaults.peers)),
        daemon_port=int(data.get("daemon_port", defaults.daemon_port)),
        thresholds=_coerce_thresholds(data.get("thresholds", {})),
        notifier=_coerce_notifier(data.get("notifier", {})),
        paths=_coerce_paths(data.get("paths", {})),
    )
