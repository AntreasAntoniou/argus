"""Intended-behavior tests for argus.diffs (stub → xfail until implemented)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from argus.diffs import DiffCache


@pytest.mark.xfail(reason="stub", strict=False)
def test_diffstat_reports_working_tree_changes(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "f.txt").write_text("one\ntwo\n")
    cache = DiffCache()
    stat = cache.get(tmp_path)
    assert stat.dirty is True
    assert stat.added_lines >= 1


@pytest.mark.xfail(reason="stub", strict=False)
def test_non_git_dir_is_benign(tmp_path: Path) -> None:
    stat = DiffCache().get(tmp_path)
    assert stat.branch is None and stat.dirty is False
