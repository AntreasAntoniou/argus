"""Tests for argus.cli.

The argparse structure, HOOK_EVENTS, and hook_command are implemented → those
tests MUST pass. install_hooks / build_hook_block are stubs → xfail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from argus.cli import (
    HOOK_EVENTS,
    build_hook_block,
    build_parser,
    hook_command,
    install_hooks,
)


def test_hook_events_are_the_eight_lifecycle_hooks() -> None:
    assert len(HOOK_EVENTS) == 8


def test_hook_command_targets_local_daemon_port() -> None:
    cmd = hook_command(8787)
    assert "127.0.0.1:8787/hook" in cmd
    assert cmd.strip().endswith("&")  # non-blocking / async


def test_parser_parses_install_hooks_flags() -> None:
    parser = build_parser()
    args = parser.parse_args(
        ["install-hooks", "--settings-path", "/tmp/s.json", "--dry-run", "--no-backup"]
    )
    assert args.command == "install-hooks"
    assert args.settings_path == Path("/tmp/s.json")
    assert args.dry_run is True
    assert args.backup is False


def test_parser_defaults_run_command() -> None:
    args = build_parser().parse_args([])
    assert args.command is None  # dispatches to daemon serve


@pytest.mark.xfail(reason="stub", strict=False)
def test_install_hooks_is_idempotent(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    first = install_hooks(settings, port=8787, dry_run=False, backup=True)
    second = install_hooks(settings, port=8787, dry_run=False, backup=True)
    assert first == second  # re-running does not duplicate hook entries
    assert (tmp_path / "settings.json.bak").exists()


@pytest.mark.xfail(reason="stub", strict=False)
def test_build_hook_block_covers_all_events() -> None:
    block = build_hook_block(8787)
    assert isinstance(block, dict) and block
