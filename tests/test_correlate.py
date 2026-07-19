"""Tests for argus.correlate — mapping live tmux panes to session ids."""

from __future__ import annotations

import os
from pathlib import Path

from argus.correlate import (
    PaneSession,
    Proc,
    correlate,
    cwd_to_project_slug,
    freshest_session_for_cwd,
    is_agent_pane,
    parse_lsof_cwds,
    session_id_from_command,
)
from argus.ingest.tmux import Pane

SID = "7b870855-ebda-4a67-ae79-63f0a3c7d4ad"


def _pane(pane_id: str, title: str, pid: int | None) -> Pane:
    return Pane(
        pane_id=pane_id,
        session_name="0",
        window_index=0,
        title=title,
        pid=pid,
    )


def test_cwd_to_project_slug_matches_claude_encoding() -> None:
    assert cwd_to_project_slug("/Users/antreas") == "-Users-antreas"
    assert cwd_to_project_slug("/") == "-"
    assert (
        cwd_to_project_slug("/Users/antreas/.claude/skills/visual-qa")
        == "-Users-antreas--claude-skills-visual-qa"
    )


def test_is_agent_pane_detects_version_comm() -> None:
    assert is_agent_pane(_pane("%1", "2.1.211", 100))
    assert is_agent_pane(_pane("%2", "node", 101))
    assert is_agent_pane(_pane("%3", "claude", 102))
    assert not is_agent_pane(_pane("%4", "vim", 103))
    assert not is_agent_pane(_pane("%5", "-zsh", 104))
    assert not is_agent_pane(_pane("%6", "workmux", 105))


def test_session_id_from_command() -> None:
    assert session_id_from_command(f"2.1.2 --session-id {SID} --resume x") == SID
    # subagents carry the PARENT id (same board entry)
    assert (
        session_id_from_command(f"2.1.2 --agent-id g0@s --parent-session-id {SID}")
        == SID
    )
    assert session_id_from_command("-zsh") is None
    assert session_id_from_command("node index.js") is None


def test_parse_lsof_cwds_parses_field_output() -> None:
    out = "p100\nn/Users/antreas\np101\nn/tmp/work\n"
    assert parse_lsof_cwds(out) == {100: "/Users/antreas", 101: "/tmp/work"}


def test_freshest_session_picks_newest_transcript(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    d = root / cwd_to_project_slug("/work/x")
    d.mkdir(parents=True)
    old = d / "old-session.jsonl"
    new = d / "new-session.jsonl"
    old.write_text("{}\n")
    new.write_text("{}\n")
    os.utime(old, (1000, 1000))
    os.utime(new, (2000, 2000))
    assert freshest_session_for_cwd("/work/x", projects_root=root) == "new-session"


def test_freshest_session_missing_dir_is_none(tmp_path: Path) -> None:
    assert freshest_session_for_cwd("/nope", projects_root=tmp_path) is None


def test_correlate_reads_session_id_from_process_argv() -> None:
    # pane %1's shell (pid 100) has a claude child (pid 200) carrying --session-id.
    panes = [
        _pane("%1", "2.1.211", 100),
        _pane("%2", "vim", 300),  # not an agent → ignored
    ]
    procs = {
        100: Proc(100, 1, "-zsh"),
        200: Proc(200, 100, f".../2.1.211 --session-id {SID} --resume x.jsonl"),
    }
    result = correlate(panes, procs=procs)
    assert result == {SID: PaneSession(pane_id="%1", session_id=SID)}


def test_correlate_rolls_team_subagents_up_to_parent() -> None:
    # Two team panes, each a subagent of the same parent session → one entry.
    panes = [_pane("%1", "2.1.211", 100), _pane("%2", "2.1.211", 300)]
    procs = {
        100: Proc(100, 1, "-zsh"),
        200: Proc(200, 100, f".../2.1.211 --agent-id a@s --parent-session-id {SID}"),
        300: Proc(300, 1, "-zsh"),
        400: Proc(400, 300, f".../2.1.211 --agent-id b@s --parent-session-id {SID}"),
    }
    result = correlate(panes, procs=procs)
    assert set(result) == {SID}


def test_correlate_falls_back_to_cwd_when_argv_has_no_id(tmp_path: Path) -> None:
    root = tmp_path / "projects"
    d = root / cwd_to_project_slug("/work/x")
    d.mkdir(parents=True)
    (d / "sess-fresh.jsonl").write_text("{}\n")

    panes = [_pane("%1", "2.1.211", 100)]
    procs = {
        100: Proc(100, 1, "-zsh"),
        200: Proc(200, 100, ".../2.1.211 --chrome"),  # no session flag
    }

    def fake_resolver(pids: list[int]) -> dict[int, str]:
        return {100: "/work/x"}

    result = correlate(
        panes, procs=procs, projects_root=root, cwd_resolver=fake_resolver
    )
    assert result == {
        "sess-fresh": PaneSession(pane_id="%1", session_id="sess-fresh", cwd="/work/x")
    }


def test_correlate_no_projects_root_skips_fallback() -> None:
    panes = [_pane("%1", "2.1.211", 100)]
    procs = {100: Proc(100, 1, "-zsh"), 200: Proc(200, 100, ".../2.1.211 --chrome")}
    assert correlate(panes, procs=procs) == {}
