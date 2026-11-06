"""Ports: the seams where pure services meet the impure world.

Infrastructure adapters implement these Protocols; services depend only
on the seam, never on an adapter (ADR-0002).
"""

from typing import Protocol

from notify.models.notification import DiscordMessage, NotificationOutcome


class MessageDispatcher(Protocol):
    """Anything that can carry a prepared message to the owner."""

    def dispatch(self, *, message: DiscordMessage) -> NotificationOutcome:
        """Deliver one message and report a machine-checkable outcome."""
        ...
