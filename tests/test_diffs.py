"""Intended-behavior tests for argus.diffs (TTL-cached read-only git diff layer)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import argus.diffs as diffs
from argus.diffs import DiffCache


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)


def test_diffstat_reports_working_tree_changes(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("one\ntwo\n")
    cache = DiffCache()
    stat = cache.get(tmp_path)
    assert stat.dirty is True
    assert stat.added_lines >= 1
    assert stat.files_changed >= 1


def test_non_git_dir_is_benign(tmp_path: Path) -> None:
    stat = DiffCache().get(tmp_path)
    assert stat.branch is None and stat.dirty is False
    assert stat.added_lines == 0 and stat.files_changed == 0


def test_missing_dir_never_raises(tmp_path: Path) -> None:
    stat = DiffCache().get(tmp_path / "does-not-exist")
    assert stat == diffs.DiffStat(branch=None)


def _counting_run(monkeypatch: pytest.MonkeyPatch, counter: list[int]) -> None:
    real = diffs.subprocess.run

    def wrapper(*args: object, **kwargs: object):
        counter[0] += 1
        return real(*args, **kwargs)

    monkeypatch.setattr(diffs.subprocess, "run", wrapper)


def test_ttl_serves_second_call_from_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\n")
    cache = DiffCache(ttl_seconds=60.0)

    calls = [0]
    _counting_run(monkeypatch, calls)

    first = cache.get(tmp_path)
    after_first = calls[0]
    assert after_first > 0  # a miss shells out

    second = cache.get(tmp_path)
    assert calls[0] == after_first  # within ttl: no re-shelling
    assert second == first


def test_invalidate_forces_recompute(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\n")
    cache = DiffCache(ttl_seconds=60.0)

    calls = [0]
    _counting_run(monkeypatch, calls)

    cache.get(tmp_path)
    after_first = calls[0]

    cache.invalidate(tmp_path)
    cache.get(tmp_path)
    assert calls[0] > after_first  # eviction forces a fresh compute

    # invalidate() with no argument clears every entry.
    baseline = calls[0]
    cache.invalidate()
    cache.get(tmp_path)
    assert calls[0] > baseline


def test_stale_entry_recomputes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "f.txt").write_text("a\n")

    calls = [0]
    _counting_run(monkeypatch, calls)

    clock = [1000.0]
    monkeypatch.setattr(diffs.time, "monotonic", lambda: clock[0])

    cache = DiffCache(ttl_seconds=3.0)
    cache.get(tmp_path)
    after_first = calls[0]

    clock[0] += 5.0  # advance past ttl
    cache.get(tmp_path)
    assert calls[0] > after_first
