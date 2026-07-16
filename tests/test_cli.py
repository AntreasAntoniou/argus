"""Tests for argus.cli.

The argparse structure, HOOK_EVENTS, and hook_command are implemented → those
tests MUST pass. install_hooks / build_hook_block are stubs → xfail.
"""

from __future__ import annotations

import json
from pathlib import Path

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


def test_install_hooks_is_idempotent(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text("{}")
    first = install_hooks(settings, port=8787, dry_run=False, backup=True)
    second = install_hooks(settings, port=8787, dry_run=False, backup=True)
    assert first == second  # re-running does not duplicate hook entries
    assert (tmp_path / "settings.json.bak").exists()


def test_build_hook_block_covers_all_events() -> None:
    block = build_hook_block(8787)
    assert isinstance(block, dict) and block
    # Every one of the eight lifecycle hooks is keyed, each POSTing to /hook.
    assert {e.value for e in HOOK_EVENTS} == set(block)
    for groups in block.values():
        command = groups[0]["hooks"][0]["command"]
        assert "127.0.0.1:8787/hook" in command


def test_install_hooks_preserves_user_hooks_and_adds_argus_once(
    tmp_path: Path,
) -> None:
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps(
            {
                "model": "opus",
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Bash",
                            "hooks": [{"type": "command", "command": "my-linter"}],
                        }
                    ]
                },
            }
        )
    )
    merged = install_hooks(settings, port=8787, dry_run=False, backup=True)

    # Unrelated top-level settings survive untouched.
    assert merged["model"] == "opus"
    # The user's own PreToolUse hook is preserved alongside the argus one.
    commands = [
        h["command"]
        for group in merged["hooks"]["PreToolUse"]
        for h in group["hooks"]
    ]
    assert "my-linter" in commands
    assert any("/hook" in c for c in commands)

    # Re-running does not add a second argus entry to PreToolUse.
    again = install_hooks(settings, port=8787, dry_run=False, backup=True)
    assert again == merged


def test_install_hooks_dry_run_does_not_write(tmp_path: Path) -> None:
    settings = tmp_path / "settings.json"
    result = install_hooks(settings, port=8787, dry_run=True, backup=True)
    assert isinstance(result, dict) and result["hooks"]
    assert not settings.exists()  # dry-run never touches disk
    assert not (tmp_path / "settings.json.bak").exists()
