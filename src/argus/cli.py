"""Console entry points — ``argusd`` (daemon) and ``argus`` (TUI).

Argparse structure and config loading are REAL (and testable). The effectful
bodies — serving the app, running the TUI, writing the merged settings file —
are precise stubs. ``argusd install-hooks`` idempotently merges the async hook
set into a Claude Code settings path, with backup and ``--dry-run``.
"""

from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path

from argus.config import DEFAULT_CONFIG_PATH, ArgusConfig, load_config
from argus.models import HookEvent

# The async hook set argus installs (DESIGN.md §hook pack). Each of the eight
# lifecycle events POSTs to the local daemon's /hook endpoint. Real data so the
# merger and tests can assert against it.
HOOK_EVENTS: tuple[HookEvent, ...] = (
    HookEvent.SESSION_START,
    HookEvent.USER_PROMPT_SUBMIT,
    HookEvent.PRE_TOOL_USE,
    HookEvent.POST_TOOL_USE,
    HookEvent.NOTIFICATION,
    HookEvent.STOP,
    HookEvent.SUBAGENT_STOP,
    HookEvent.SESSION_END,
)


def hook_command(port: int, token: str = "") -> str:
    """Return the async shell command a single hook runs to POST to the daemon.

    Fully implemented (pure). Kept here so :func:`install_hooks` and tests share
    one definition of the injected command.

    Args:
        port: The local ``argusd`` port to POST to.
        token: Shared federation secret; added as an ``X-Argus-Token`` header so
            local hooks authenticate against a token-gated daemon. Empty = none.

    Returns:
        A non-blocking curl one-liner that forwards the hook JSON on stdin.
    """

    auth = f"-H 'X-Argus-Token: {token}' " if token else ""
    return (
        "curl -s -m 2 -X POST "
        f"http://127.0.0.1:{port}/hook "
        f"-H 'Content-Type: application/json' {auth}--data-binary @- >/dev/null 2>&1 &"
    )


def build_hook_block(port: int, token: str = "") -> dict[str, object]:
    """Build the ``hooks`` settings fragment Argus merges in.

    STUB. Implementers: produce the exact ``settings.json`` ``hooks`` structure
    (per-event matcher lists invoking :func:`hook_command`) that Claude Code
    expects, keyed by the eight :data:`HOOK_EVENTS`.

    Args:
        port: Local daemon port for the hook command.

    Returns:
        A dict suitable for merging under the top-level ``"hooks"`` key: each of
        the eight event names maps to a single matcher group invoking
        :func:`hook_command`.
    """

    command = hook_command(port, token)
    return {
        event.value: [
            {"matcher": "*", "hooks": [{"type": "command", "command": command}]}
        ]
        for event in HOOK_EVENTS
    }


def install_hooks(
    settings_path: Path,
    *,
    port: int,
    token: str = "",
    dry_run: bool = False,
    backup: bool = True,
) -> dict[str, object]:
    """Idempotently merge the Argus hook set into a Claude Code settings file.

    STUB. Implementers: load existing JSON (empty dict if absent), deep-merge
    :func:`build_hook_block` WITHOUT clobbering unrelated user hooks, and only
    add Argus entries that are not already present (idempotent). When ``backup``,
    copy the original to ``<path>.bak`` first. When ``dry_run``, compute and
    return the merged result but do NOT write.

    Args:
        settings_path: Target settings file
            (:attr:`argus.config.Paths.settings_path`).
        port: Local daemon port the hooks POST to.
        dry_run: Compute the merge but do not write.
        backup: Back up the original file before writing.

    Returns:
        The merged settings dict (whether or not it was written).
    """

    settings_path = Path(settings_path)
    existing: dict[str, object] = {}
    if settings_path.exists():
        text = settings_path.read_text().strip()
        existing = json.loads(text) if text else {}

    merged = copy.deepcopy(existing)
    hooks = merged.setdefault("hooks", {})
    command = hook_command(port, token)

    for event, groups in build_hook_block(port, token).items():
        event_groups = hooks.setdefault(event, [])
        already_present = any(
            isinstance(group, dict)
            and any(h.get("command") == command for h in group.get("hooks", []))
            for group in event_groups
        )
        if not already_present:
            event_groups.extend(groups)

    if not dry_run:
        if backup and settings_path.exists():
            backup_path = settings_path.with_name(settings_path.name + ".bak")
            backup_path.write_text(settings_path.read_text())
        settings_path.parent.mkdir(parents=True, exist_ok=True)
        settings_path.write_text(json.dumps(merged, indent=2) + "\n")

    return merged


def build_parser() -> argparse.ArgumentParser:
    """Build the ``argusd`` argument parser (real, testable).

    Subcommands:
        - ``run`` (default): serve the daemon.
        - ``install-hooks``: merge the hook set into a settings file.

    Returns:
        The configured :class:`argparse.ArgumentParser`.
    """

    parser = argparse.ArgumentParser(prog="argusd", description="Argus daemon.")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to config.toml (default: ~/.argus/config.toml).",
    )
    sub = parser.add_subparsers(dest="command")

    run_p = sub.add_parser("run", help="Run the argusd daemon (default).")
    run_p.add_argument("--port", type=int, default=None, help="Override daemon port.")
    run_p.add_argument(
        "--host",
        type=str,
        default=None,
        help="Override bind address (0.0.0.0 to accept federation peers).",
    )

    hooks_p = sub.add_parser(
        "install-hooks", help="Merge the async hook set into settings.json."
    )
    hooks_p.add_argument(
        "--settings-path",
        type=Path,
        default=None,
        help="Settings file to merge into (default: config.paths.settings_path).",
    )
    hooks_p.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the merged result without writing.",
    )
    hooks_p.add_argument(
        "--no-backup",
        dest="backup",
        action="store_false",
        help="Do not write a .bak copy before modifying.",
    )
    hooks_p.set_defaults(backup=True)

    sub.add_parser(
        "compact",
        help="Slim stored raw payloads and reclaim journal file space.",
    )
    return parser


def daemon_main(argv: list[str] | None = None) -> int:
    """Entry point for ``argusd``.

    Parses args, loads config (real), then dispatches: ``install-hooks`` runs the
    merger; otherwise serves the daemon via ``uvicorn`` +
    :func:`argus.daemon.create_app`.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """

    args = build_parser().parse_args(argv)
    config: ArgusConfig = load_config(args.config)

    if args.command == "install-hooks":
        settings_path = args.settings_path or config.paths.settings_path
        result = install_hooks(
            settings_path,
            port=config.daemon_port,
            token=config.federation_token,
            dry_run=args.dry_run,
            backup=args.backup,
        )
        if args.dry_run:
            print(json.dumps(result, indent=2))
        return 0

    if args.command == "compact":
        from argus.store import SessionStore

        store = SessionStore(config.paths.journal_path, config.machine)
        try:
            rewritten, reclaimed = store.compact()
        finally:
            store.close()
        print(
            f"compacted {config.paths.journal_path}: "
            f"{rewritten} rows slimmed, {reclaimed / 1e6:.1f} MB reclaimed"
        )
        return 0

    import uvicorn

    from argus.daemon import create_app

    port = getattr(args, "port", None) or config.daemon_port
    host = getattr(args, "host", None) or config.daemon_host
    uvicorn.run(create_app(config), host=host, port=port)
    return 0


def tui_main(argv: list[str] | None = None) -> int:
    """Entry point for ``argus`` (the TUI).

    Parses a minimal arg set, loads config (real), and runs
    :class:`argus.tui.ArgusApp` against the local daemon's SSE stream.

    Args:
        argv: Argument vector (defaults to ``sys.argv[1:]``).

    Returns:
        Process exit code.
    """

    parser = argparse.ArgumentParser(prog="argus", description="Argus TUI board.")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    args = parser.parse_args(argv)
    config = load_config(args.config)

    from argus.tui import ArgusApp

    ArgusApp(config).run()
    return 0
