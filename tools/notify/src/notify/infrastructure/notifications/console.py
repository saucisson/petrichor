"""Console adapter: delivers notifications as structured log events.

This is the fallback channel - it requires no configuration and is always
available. It writes through ``structlog`` (never ``print``) so the event
is routed through the same pipeline as every other log.
"""

import structlog

from notify.models.notification import DeliveryChannel, DeliverySuccess, DiscordMessage
from notify.services.ports import MessageDispatcher

logger = structlog.get_logger(__name__)


class ConsoleDispatcher:
    """Delivers messages by logging them, for local and smoke-test usage."""

    def dispatch(self, *, message: DiscordMessage) -> DeliverySuccess:
        """Log every embed as a milestone event."""
        logger.info(
            "console_notification",
            embed_title=message.embeds[0].title,
            embed_description=message.embeds[0].description,
            embed_field_count=len(message.embeds[0].fields),
        )
        return DeliverySuccess(delivery_channel=DeliveryChannel.CONSOLE)
