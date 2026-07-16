"""Tests for argus.config — the implemented config loader. These MUST pass."""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.config import (
    ArgusConfig,
    NotifierKind,
    Thresholds,
    load_config,
)


def test_missing_file_returns_defaults(tmp_path: Path) -> None:
    cfg = load_config(tmp_path / "does_not_exist.toml")
    assert isinstance(cfg, ArgusConfig)
    assert cfg.peers == []
    assert cfg.daemon_port == 8787
    assert cfg.notifier.kind is NotifierKind.NOOP
    assert cfg.thresholds.notify_batch_seconds == 300
    assert cfg.thresholds.dead_after_seconds == 600
    assert cfg.thresholds.poll_interval_seconds == 5
    assert cfg.paths.claude_projects_root == Path.home() / ".claude" / "projects"


def test_defaults_dataclass_values() -> None:
    assert Thresholds().notify_batch_seconds == 300
    assert ArgusConfig().notifier.kind is NotifierKind.NOOP


def test_partial_file_overrides_only_specified_keys(tmp_path: Path) -> None:
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(
        "\n".join(
            [
                'machine = "astrape"',
                'peers = ["mac:8787", "forge:8787"]',
                "daemon_port = 9000",
                "",
                "[thresholds]",
                "dead_after_seconds = 30",
                "",
                "[notifier]",
                'kind = "whatsapp"',
                'whatsapp_command = "notify {message}"',
                "",
                "[paths]",
                'claude_projects_root = "/home/dev/.claude/projects"',
            ]
        )
    )
    cfg = load_config(cfg_path)
    assert cfg.machine == "astrape"
    assert cfg.peers == ["mac:8787", "forge:8787"]
    assert cfg.daemon_port == 9000
    # Overridden threshold applies; unspecified ones keep defaults.
    assert cfg.thresholds.dead_after_seconds == 30
    assert cfg.thresholds.notify_batch_seconds == 300
    assert cfg.notifier.kind is NotifierKind.WHATSAPP
    assert cfg.notifier.whatsapp_command == "notify {message}"
    assert cfg.paths.claude_projects_root == Path("/home/dev/.claude/projects")


def test_invalid_notifier_kind_raises(tmp_path: Path) -> None:
    cfg_path = tmp_path / "bad.toml"
    cfg_path.write_text('[notifier]\nkind = "smoke-signals"\n')
    with pytest.raises(ValueError):
        load_config(cfg_path)


def test_accepts_str_path(tmp_path: Path) -> None:
    cfg = load_config(str(tmp_path / "nope.toml"))
    assert cfg.daemon_port == 8787
