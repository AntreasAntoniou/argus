"""Intended-behavior tests for argus.daemon (stub → xfail until implemented)."""

from __future__ import annotations

import pytest

from argus.config import ArgusConfig
from argus.daemon import create_app


@pytest.mark.xfail(reason="stub", strict=False)
def test_create_app_returns_fastapi_with_hook_route() -> None:
    app = create_app(ArgusConfig())
    paths = {getattr(r, "path", None) for r in app.routes}
    assert "/hook" in paths
    assert "/api/state" in paths
