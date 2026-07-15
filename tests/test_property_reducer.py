"""Hypothesis property tests for the reducer state machine (xfail until built).

The invariant: folding an arbitrary sequence of well-formed events through
:func:`argus.reducer.reduce` always yields a valid :class:`SessionSnapshot`
whose status is a legal :class:`SessionStatus`, whose ``updated_at`` never moves
backwards, and where ``tool_name`` is set iff the status is ``TOOL``.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from argus.models import Event, HookEvent, SessionSnapshot, SessionStatus
from argus.reducer import reduce

_HOOK_NAMES = [h.value for h in HookEvent]


@st.composite
def event_seqs(draw: st.DrawFn) -> list[Event]:
    names = draw(st.lists(st.sampled_from(_HOOK_NAMES), min_size=1, max_size=12))
    return [
        Event(
            session_id="s1",
            machine="mac",
            hook_event_name=n,
            tool_name="Edit"
            if n in (HookEvent.PRE_TOOL_USE, HookEvent.POST_TOOL_USE)
            else None,
        )
        for n in names
    ]


@given(events=event_seqs())
def test_reduce_preserves_invariants(events: list[Event]) -> None:
    snap: SessionSnapshot | None = None
    prev_ts = None
    for ev in events:
        snap = reduce(snap, ev)
        assert isinstance(snap.status, SessionStatus)
        if prev_ts is not None:
            assert snap.updated_at >= prev_ts
        prev_ts = snap.updated_at
        if snap.status is SessionStatus.TOOL:
            assert snap.tool_name is not None
        else:
            assert snap.tool_name is None
