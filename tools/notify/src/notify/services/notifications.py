"""Pure notification orchestration: no I/O, only decisions and narration.

Every ``DEBUG`` event here is an executable comment (ADR-0005) - the tests
pin them, so the narration cannot rot the way prose comments do.
"""

from typing import TypeIs
from collections.abc import Mapping

import structlog

from notify.models.notification import (
    DeliveryFailure,
    DiscordEmbedField,
    DiscordMessage,
    NotificationOutcome,
    NotificationRequest,
    NotificationSeverity,
)
from notify.services.ports import MessageDispatcher

logger = structlog.get_logger(__name__)

EMBED_COLOR_BY_SEVERITY: Mapping[NotificationSeverity, int] = {
    NotificationSeverity.QUESTION: 0xFEE75C,
    NotificationSeverity.URGENT: 0xED4245,
    NotificationSeverity.INFO: 0x3498DB,
}

EMBED_ISSUE_FIELD_NAME = "Issue"


def color_for_severity(*, severity: NotificationSeverity) -> int:
    """Return the embed color reserved for this severity."""
    match severity:
        case NotificationSeverity.QUESTION:
            return EMBED_COLOR_BY_SEVERITY[NotificationSeverity.QUESTION]
        case NotificationSeverity.URGENT:
            return EMBED_COLOR_BY_SEVERITY[NotificationSeverity.URGENT]
        case NotificationSeverity.INFO:
            return EMBED_COLOR_BY_SEVERITY[NotificationSeverity.INFO]
        case unreachable:
            assert_never(unreachable)


def build_discord_message(*, request: NotificationRequest) -> DiscordMessage:
    """Translate a notification request into the Discord payload contract."""
    embed_fields: tuple[DiscordEmbedField, ...] = ()
    if request.issue_identifier is not None:
        embed_fields = (
            DiscordEmbedField(name=EMBED_ISSUE_FIELD_NAME, value=request.issue_identifier),
        )
    severity_title = (
        f"{request.severity.display_emoji} {request.severity.display_label}"
    )
    embed = DiscordEmbed(
        title=severity_title,
        description=request.message,
        color=color_for_severity(severity=request.severity),
        fields=embed_fields,
    )
    return DiscordMessage(content=severity_title, embeds=(embed,))


def is_delivery_failure(outcome: NotificationOutcome) -> TypeIs[DeliveryFailure]:
    """Narrow the outcome union to the failure variant."""
    return outcome.kind == "failure"


def is_urgent(*, request: NotificationRequest) -> bool:
    """Whether the request must reach a push channel (ADR-0012)."""
    return request.severity is NotificationSeverity.URGENT


def dispatch_notification(
    *,
    request: NotificationRequest,
    dispatcher: MessageDispatcher,
) -> NotificationOutcome:
    """Build, send and narrate one notification."""
    logger.debug(
        "notification_dispatch_started",
        severity=request.severity.value,
        issue_identifier=request.issue_identifier,
    )
    message = build_discord_message(request=request)
    outcome = dispatcher.dispatch(message=message)
    if is_delivery_failure(outcome):
        logger.error(
            "notification_delivery_failed",
            failure_reason=outcome.failure_reason,
        )
        return outcome
    logger.info(
        "notification_delivered",
        delivery_channel=outcome.delivery_channel.value,
    )
    return outcome
