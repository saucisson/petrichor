"""Unit tests for the pure notification service.

These tests assert *log narration* as well as behavior (ADR-0005): the
DEBUG events are the executable comments of the service, so they are part
of its contract and must not silently disappear.
"""

from notify.models.notification import (
    DeliveryChannel,
    DeliveryFailure,
    DeliverySuccess,
    DiscordMessage,
    NotificationOutcome,
    NotificationRequest,
    NotificationSeverity,
)
from notify.models.types import IssueIdentifier, MessageText
from notify.services.notifications import (
    build_discord_message,
    color_for_severity,
    dispatch_notification,
    is_delivery_failure,
    is_urgent,
)

SUCCESS = DeliverySuccess(delivery_channel=DeliveryChannel.CONSOLE)
FAILURE = DeliveryFailure(failure_reason="http_error")


class FakeDispatcher:
    """Test double for the MessageDispatcher port."""

    def __init__(self, outcome: NotificationOutcome) -> None:
        self.outcome = outcome
        self.received_messages: list[DiscordMessage] = []

    def dispatch(self, *, message: DiscordMessage) -> NotificationOutcome:
        """Record the message, then replay the scripted outcome."""
        self.received_messages.append(message)
        return self.outcome


def captured_event_names(captured_events: list[dict[str, object]]) -> set[str]:
    """Extract the set of event names from captured structlog events."""
    return {str(event["event"]) for event in captured_events}


class TestDispatchNotification:
    def test_success_returns_outcome_and_passes_message(
        self,
        sample_notification_request: NotificationRequest,
        capture_logs: list[dict[str, object]],
    ) -> None:
        dispatcher = FakeDispatcher(SUCCESS)

        outcome = dispatch_notification(
            request=sample_notification_request, dispatcher=dispatcher
        )

        assert outcome.kind == "success"
        assert dispatcher.received_messages[0].embeds[0].description == (
            sample_notification_request.message
        )
        assert captured_event_names(capture_logs) >= {
            "notification_dispatch_started",
            "notification_delivered",
        }

    def test_failure_narrates_an_error(
        self,
        sample_notification_request: NotificationRequest,
        capture_logs: list[dict[str, object]],
    ) -> None:
        dispatcher = FakeDispatcher(FAILURE)

        outcome = dispatch_notification(
            request=sample_notification_request, dispatcher=dispatcher
        )

        assert outcome.kind == "failure"
        assert "notification_delivery_failed" in captured_event_names(capture_logs)


class TestBuildDiscordMessage:
    def test_title_contains_severity_label(
        self, sample_notification_request: NotificationRequest
    ) -> None:
        message = build_discord_message(request=sample_notification_request)

        assert sample_notification_request.severity.display_label in (
            message.embeds[0].title
        )
        assert message.embeds[0].description == sample_notification_request.message

    def test_issue_identifier_becomes_embed_field(
        self, sample_notification_request: NotificationRequest
    ) -> None:
        message = build_discord_message(request=sample_notification_request)

        field_values = [field.value for field in message.embeds[0].fields]
        assert field_values == [sample_notification_request.issue_identifier]

    def test_no_issue_identifier_means_no_field(self) -> None:
        request = NotificationRequest(
            severity=NotificationSeverity.INFO,
            message=MessageText("Daily report generated."),
            issue_identifier=None,
        )

        message = build_discord_message(request=request)

        assert message.embeds[0].fields == ()


class TestColorForSeverity:
    def test_each_severity_has_a_distinct_known_color(self) -> None:
        colors = {
            severity: color_for_severity(severity=severity)
            for severity in NotificationSeverity
        }

        assert len(set(colors.values())) == len(NotificationSeverity)
        assert all(0 <= color <= 0xFFFFFF for color in colors.values())


class TestOutcomePredicates:
    def test_is_delivery_failure_narrows_the_union(self) -> None:
        assert is_delivery_failure(FAILURE)
        assert not is_delivery_failure(SUCCESS)

    def test_is_urgent_only_for_urgent_severity(
        self, sample_notification_request: NotificationRequest
    ) -> None:
        assert not is_urgent(request=sample_notification_request)
        urgent_request = NotificationRequest(
            severity=NotificationSeverity.URGENT,
            message=MessageText("All merges are stopped."),
            issue_identifier=IssueIdentifier("PR-7"),
        )
        assert is_urgent(request=urgent_request)
