"""Discord adapter: posts prepared messages to an incoming webhook.

The URL arrives as a ``SecretStr`` and is unwrapped once, into the domain
primitive ``WebhookUrl``. The adapter owns the transport concerns (timeouts,
error classes) and reports only machine-checkable outcomes.
"""

import httpx
from pydantic import SecretStr
import structlog

from notify.models.notification import (
    DeliveryChannel,
    DeliveryFailure,
    DeliverySuccess,
    DiscordMessage,
)
from notify.models.types import WebhookUrl
from notify.services.ports import MessageDispatcher

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT_SECONDS = 10.0


class DiscordWebhookDispatcher:
    """Delivers messages through a Discord incoming webhook."""

    def __init__(self, *, webhook_url: SecretStr, http_client: httpx.Client) -> None:
        self._webhook_url = WebhookUrl(webhook_url.get_secret_value())
        self._http_client = http_client

    def dispatch(self, *, message: DiscordMessage) -> DeliverySuccess | DeliveryFailure:
        """POST the payload and translate transport outcomes to domain terms."""
        logger.debug("discord_webhook_request_started", embed_count=len(message.embeds))
        try:
            response = self._http_client.post(
                self._webhook_url,
                json=message.model_dump(mode="json"),
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except httpx.HTTPError as http_error:
            logger.warning(
                "discord_webhook_request_failed",
                error_class=type(http_error).__name__,
                error_message=str(http_error),
            )
            return DeliveryFailure(failure_reason="http_error")
        logger.debug(
            "discord_webhook_response_received",
            status_code=response.status_code,
        )
        if response.is_success:
            return DeliverySuccess(delivery_channel=DeliveryChannel.DISCORD)
        logger.warning(
            "discord_webhook_rejected_payload",
            status_code=response.status_code,
        )
        return DeliveryFailure(failure_reason="http_error")
