"""Integration tests for the notification adapters.

Marked ``integration``: they exercise adapter wiring (httpx transport,
Discord payload contract) rather than pure logic, and may be deselected
with ``-m 'not integration'``.
"""

import json
from collections.abc import Any
from typing import Any as TypingAny

import httpx
import pytest
from pydantic import SecretStr

from notify.infrastructure.notifications.console import ConsoleDispatcher
from notify.infrastructure.notifications.discord import DiscordWebhookDispatcher
from notify.models.notification import (
    DeliveryChannel,
    DeliverySuccess,
    NotificationRequest,
    NotificationSeverity,
)
from notify.models.types import MessageText
from notify.services.notifications import build_discord_message

pytestmark = pytest.mark.integration

WEBHOOK_URL = "https://discord.com/api/webhooks/123/fake-token"
MESSAGE_TEXT = MessageText("Integration check: the webhook contract holds.")


def request_with_message(message_text: MessageText) -> NotificationRequest:
    return NotificationRequest(
        severity=NotificationSeverity.INFO,
        message=message_text,
        issue_identifier=None,
    )


class TestDiscordWebhookDispatcher:
    def test_success_posts_embed_description_to_webhook(self) -> None:
        received_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received_requests.append(request)
            return httpx.Response(200)

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        dispatcher = DiscordWebhookDispatcher(
            webhook_url=SecretStr(WEBHOOK_URL), http_client=http_client
        )
        message = build_discord_message(request=request_with_message(MESSAGE_TEXT))

        outcome = dispatcher.dispatch(message=message)

        assert isinstance(outcome, DeliverySuccess)
        assert outcome.delivery_channel is DeliveryChannel.DISCORD
        assert str(received_requests[0].url) == WEBHOOK_URL
        assert received_requests[0].method == "POST"
        payload: dict[str, TypingAny] = json.loads(received_requests[0].content)
        assert payload["embeds"][0]["description"] == MESSAGE_TEXT

    def test_server_error_reports_failure(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500)

        http_client = httpx.Client(transport=httpx.MockTransport(handler))
        dispatcher = DiscordWebhookDispatcher(
            webhook_url=SecretStr(WEBHOOK_URL), http_client=http_client
        )
        message = build_discord_message(request=request_with_message(MESSAGE_TEXT))

        outcome = dispatcher.dispatch(message=message)

        assert outcome.kind == "failure"
        assert outcome.failure_reason == "http_error"

    def test_connection_error_reports_failure(self) -> None:
        def connection_error_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused.")

        http_client = httpx.Client(transport=httpx.MockTransport(connection_error_handler))
        dispatcher = DiscordWebhookDispatcher(
            webhook_url=SecretStr(WEBHOOK_URL), http_client=http_client
        )
        message = build_discord_message(request=request_with_message(MESSAGE_TEXT))

        outcome = dispatcher.dispatch(message=message)

        assert outcome.kind == "failure"
        assert outcome.failure_reason == "http_error"


class TestConsoleDispatcher:
    def test_dispatch_logs_the_embed(
        self, capture_logs: list[dict[str, Any]]
    ) -> None:
        dispatcher = ConsoleDispatcher()
        message = build_discord_message(request=request_with_message(MESSAGE_TEXT))

        outcome = dispatcher.dispatch(message=message)

        assert outcome.kind == "success"
        assert outcome.delivery_channel is DeliveryChannel.CONSOLE
        console_events = [
            event for event in capture_logs if event["event"] == "console_notification"
        ]
        assert len(console_events) == 1
        assert console_events[0]["embed_description"] == MESSAGE_TEXT
