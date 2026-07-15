"""Intended-behavior tests for argus.tui (stub → xfail until implemented)."""

from __future__ import annotations

import pytest

from argus.config import ArgusConfig
from argus.tui import ArgusApp


def test_bindings_cover_board_controls() -> None:
    # BINDINGS is class-level real data (j/k nav, y/n reply, enter attach, quit).
    keys = {b[0] for b in ArgusApp.BINDINGS}
    assert {"j", "k", "y", "n", "enter", "q"} <= keys


@pytest.mark.xfail(reason="stub", strict=False)
def test_app_constructs_from_config() -> None:
    app = ArgusApp(ArgusConfig())
    assert app is not None
