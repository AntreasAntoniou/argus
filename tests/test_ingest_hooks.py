"""Intended-behavior tests for argus.ingest.hooks (stub → xfail)."""

from __future__ import annotations

import pytest

from argus.ingest.hooks import parse_hook_body
from argus.models import HookEvent


@pytest.mark.xfail(reason="stub", strict=False)
def test_parse_pretooluse_body_extracts_tool() -> None:
    body = {
        "hook_event_name": HookEvent.PRE_TOOL_USE.value,
        "session_id": "s1",
        "cwd": "/home/dev/example",
        "tool_name": "Edit",
        "tool_input": {"file_path": "/home/dev/example/a.py"},
    }
    ev = parse_hook_body(body, machine="mac")
    assert ev.session_id == "s1"
    assert ev.hook_event_name == HookEvent.PRE_TOOL_USE.value
    assert ev.tool_name == "Edit"
    assert ev.raw == body


@pytest.mark.xfail(reason="stub", strict=False)
def test_parse_missing_mandatory_field_raises() -> None:
    with pytest.raises(KeyError):
        parse_hook_body({"cwd": "/home/dev/example"}, machine="mac")
