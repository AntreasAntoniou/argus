"""Push notifier — batched WhatsApp digests when you're away.

Per ``DESIGN.md`` decision #7: WhatsApp via the agent-comms backend
(self-notify), batched ≤1 msg / ``notify_batch_seconds`` unless critical; digest
format is a count plus a per-agent question line.

:class:`NoopNotifier` is FULLY implemented (it is the default and what tests use).
:class:`WhatsAppNotifier` and :class:`NotifyBatcher` are precise stubs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from argus.config import NotifierConfig
from argus.models import SessionSnapshot, utcnow

logger = logging.getLogger("argus.notify")


@dataclass(frozen=True, slots=True)
class Digest:
    """A coalesced block digest ready to push.

    Attributes:
        blocked: The blocked sessions this digest covers.
        critical: If True, bypasses the batch-spacing throttle.
    """

    blocked: list[SessionSnapshot]
    critical: bool = False

    def render(self) -> str:
        """Render the digest as ``count + per-agent question`` (decision #7).

        Fully implemented — pure formatting, no I/O, so both notifiers share it.
        """

        n = len(self.blocked)
        if n == 0:
            return "Argus: no agents need you."
        header = f"Argus: {n} agent{'s' if n != 1 else ''} need you"
        lines = [header]
        for s in self.blocked:
            q = s.question or "(waiting)"
            lines.append(f"• {s.session_id[:8]} @{s.machine}: {q}")
        return "\n".join(lines)


@runtime_checkable
class Notifier(Protocol):
    """A push-notification backend."""

    def send(self, digest: Digest) -> bool:
        """Push a digest. Returns True on success. Must not raise on delivery
        failure — log and return False so the daemon loop is never broken."""
        ...


class NoopNotifier:
    """A notifier that only logs — the default and the test backend.

    FULLY implemented: safe, side-effect-light, and never raises. Used until a
    WhatsApp command is configured (``DESIGN.md`` principle: fresh install is
    silent).
    """

    def __init__(self) -> None:
        self.sent: list[Digest] = []

    def send(self, digest: Digest) -> bool:
        """Record and log the digest; always succeeds.

        Args:
            digest: The digest that would have been pushed.

        Returns:
            Always ``True``.
        """

        self.sent.append(digest)
        logger.info("[noop-notify]%s %s", " CRITICAL" if digest.critical else "",
                    digest.render().replace("\n", " | "))
        return True


class WhatsAppNotifier:
    """Notifier that shells out to a configurable command template.

    STUB. Implementers: substitute the rendered digest into
    :attr:`argus.config.NotifierConfig.whatsapp_command` (token ``{message}``)
    and run it via ``subprocess``. Delivery failures are logged and return
    ``False`` — never raise.
    """

    def __init__(self, config: NotifierConfig) -> None:
        """Store the command template from config.

        Args:
            config: Notifier config carrying ``whatsapp_command``.
        """

        raise NotImplementedError("Store config.whatsapp_command template")

    def send(self, digest: Digest) -> bool:
        """Render the digest and run the configured command.

        Args:
            digest: The digest to push.

        Returns:
            ``True`` if the command exited 0, else ``False``.
        """

        raise NotImplementedError("Substitute {message}, subprocess.run the template")


@dataclass(slots=True)
class NotifyBatcher:
    """Coalesces block digests to ≤1 push / ``batch_seconds`` unless critical.

    STUB. Implementers: accumulate blocked sessions; on :meth:`maybe_flush`,
    emit a :class:`Digest` only if ``batch_seconds`` elapsed since the last push
    OR any pending item is critical (``DESIGN.md`` decision #7). Dedupe by
    session id so a still-blocked agent is not re-announced every cycle.

    Attributes:
        notifier: The backend to push through.
        batch_seconds: Minimum spacing between non-critical pushes.
        last_sent_at: Timestamp of the last push (``None`` until first push).
    """

    notifier: Notifier
    batch_seconds: int = 300
    last_sent_at: datetime | None = None
    _pending: dict[str, SessionSnapshot] = field(default_factory=dict)

    def observe_blocked(self, session: SessionSnapshot) -> None:
        """Register a newly-blocked session for the next digest.

        Args:
            session: A session that has entered ``BLOCKED``.
        """

        raise NotImplementedError("Add to _pending keyed by session_id (dedup)")

    def maybe_flush(
        self, *, now: datetime | None = None, critical: bool = False
    ) -> bool:
        """Flush a digest if the throttle window elapsed or ``critical``.

        Args:
            now: Current time (defaults to :func:`argus.models.utcnow`).
            critical: Force an immediate flush regardless of spacing.

        Returns:
            ``True`` if a digest was pushed, else ``False``.
        """

        _ = now or utcnow()
        raise NotImplementedError("Throttle-check, build Digest, notifier.send, reset")
