"""Tests for argus.notify.

NoopNotifier and Digest.render are implemented → those tests MUST pass.
WhatsAppNotifier and NotifyBatcher are stubs → xfail.
"""

from __future__ import annotations

from datetime import timedelta

from argus.config import NotifierConfig
from argus.models import SessionSnapshot, SessionStatus, utcnow
from argus.notify import (
    Digest,
    NoopNotifier,
    Notifier,
    NotifyBatcher,
    WhatsAppNotifier,
)


def _blocked(sid: str, q: str) -> SessionSnapshot:
    return SessionSnapshot(session_id=sid, machine="astrape",
                           status=SessionStatus.BLOCKED, question=q)


def test_digest_render_counts_and_lists_questions() -> None:
    d = Digest(blocked=[_blocked("11111111", "Run migration?"),
                        _blocked("22222222", "Push to main?")])
    text = d.render()
    assert "2 agents need you" in text
    assert "Run migration?" in text and "Push to main?" in text


def test_digest_render_empty() -> None:
    assert "no agents" in Digest(blocked=[]).render().lower()


def test_noop_notifier_records_and_succeeds() -> None:
    n = NoopNotifier()
    assert isinstance(n, Notifier)  # satisfies the runtime-checkable protocol
    d = Digest(blocked=[_blocked("11111111", "Q?")])
    assert n.send(d) is True
    assert n.sent == [d]


def test_whatsapp_notifier_runs_command_template() -> None:
    n = WhatsAppNotifier(NotifierConfig(whatsapp_command="true {message}"))
    assert n.send(Digest(blocked=[_blocked("1", "Q?")])) is True


def test_whatsapp_notifier_substitutes_message() -> None:
    seen: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        seen.append(argv)
        return 0

    n = WhatsAppNotifier(
        NotifierConfig(whatsapp_command="send --message {message}"), runner=runner
    )
    assert n.send(Digest(blocked=[_blocked("11111111", "Run migration?")])) is True
    argv = seen[0]
    assert "{message}" not in " ".join(argv)  # placeholder was substituted
    assert any("Run migration?" in arg for arg in argv)  # digest carried through
    assert any(arg == "--message" for arg in argv)  # template structure preserved


def test_whatsapp_notifier_nonzero_exit_returns_false_no_raise() -> None:
    n = WhatsAppNotifier(
        NotifierConfig(whatsapp_command="false"), runner=lambda _argv: 3
    )
    assert n.send(Digest(blocked=[_blocked("1", "Q?")])) is False


def test_whatsapp_notifier_runner_exception_returns_false_no_raise() -> None:
    def boom(_argv: list[str]) -> int:
        raise OSError("no such command")

    n = WhatsAppNotifier(NotifierConfig(whatsapp_command="x"), runner=boom)
    assert n.send(Digest(blocked=[_blocked("1", "Q?")])) is False


def test_batcher_throttles_noncritical() -> None:
    n = NoopNotifier()
    batcher = NotifyBatcher(notifier=n, batch_seconds=300)
    batcher.observe_blocked(_blocked("1", "Q?"))
    assert batcher.maybe_flush(critical=True) is True  # critical bypasses throttle
    batcher.observe_blocked(_blocked("2", "Q2?"))
    assert batcher.maybe_flush() is False  # within window, non-critical → held


def test_batcher_dedupes_by_session_id() -> None:
    n = NoopNotifier()
    batcher = NotifyBatcher(notifier=n, batch_seconds=300)
    batcher.observe_blocked(_blocked("1", "First?"))
    batcher.observe_blocked(_blocked("1", "Updated?"))  # same id → coalesced
    assert batcher.maybe_flush(critical=True) is True
    assert len(n.sent) == 1
    only = n.sent[0]
    assert len(only.blocked) == 1
    assert only.blocked[0].question == "Updated?"


def test_batcher_flushes_when_window_elapsed() -> None:
    n = NoopNotifier()
    start = utcnow()
    batcher = NotifyBatcher(notifier=n, batch_seconds=300, last_sent_at=start)
    batcher.observe_blocked(_blocked("1", "Q?"))
    # Still inside the window → held.
    assert batcher.maybe_flush(now=start + timedelta(seconds=299)) is False
    # Window elapsed → pushes even without the critical flag.
    assert batcher.maybe_flush(now=start + timedelta(seconds=301)) is True


def test_batcher_blocked_session_forces_immediate_flush_past_throttle() -> None:
    n = NoopNotifier()
    start = utcnow()
    # last_sent_at just now → deep inside the throttle window.
    batcher = NotifyBatcher(notifier=n, batch_seconds=300, last_sent_at=start)
    batcher.observe_blocked(_blocked("1", "Needs a human?"))
    # A BLOCKED (needs_you) session is critical → immediate flush despite throttle.
    assert batcher.maybe_flush(now=start + timedelta(seconds=5), critical=True) is True
    assert n.sent[0].critical is True


def test_batcher_noop_when_no_pending() -> None:
    batcher = NotifyBatcher(notifier=NoopNotifier(), batch_seconds=0)
    assert batcher.maybe_flush(critical=True) is False
