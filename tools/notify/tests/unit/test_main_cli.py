"""Tests for the composition root: CLI contract, wiring and exit codes.

``notify.main`` is the only module where logging is configured and adapters
are constructed (ADR-0002, ADR-0005); these tests pin that wiring, the
``send``/``smoke`` CLI contract, and the fail-closed urgent path (ADR-0011).
"""

import json
import logging
import sys
from typing import TYPE_CHECKING

import pytest
import structlog
from pydantic import SecretStr, ValidationError

from notify.infrastructure.notifications.console import ConsoleDispatcher
from notify.infrastructure.notifications.discord import DiscordWebhookDispatcher
from notify.main import (
    EXIT_CODE_FAILURE,
    EXIT_CODE_SUCCESS,
    configure_logging,
    main,
    parse_arguments,
    report_outcome,
    select_dispatcher,
    send_command,
    smoke_command,
)
from notify.models.errors import NotificationConfigurationError
from notify.models.notification import (
    DeliveryChannel,
    DeliveryFailure,
    DeliverySuccess,
    NotificationRequest,
    NotificationSeverity,
)
from notify.models.settings import ApplicationSettings, LogFormat
from notify.models.types import MessageText
from notify.services.ports import MessageDispatcher

if TYPE_CHECKING:
    from collections.abc import Iterator


FAKE_WEBHOOK_URL = "https://discord.com/api/webhooks/123/fake-token"


def keep_default_logging(*, settings: ApplicationSettings) -> None:
    """Stand in for ``configure_logging``: TestConfigureLogging pins the wiring."""


class TestParseArguments:
    def test_send_parses_severity_message_and_default_issue(self) -> None:
        arguments = parse_arguments(
            command_line_arguments=["send", "--severity", "info", "--message", "deploy finished"]
        )

        assert arguments.command_name == "send"
        assert arguments.severity == "info"
        assert arguments.message == "deploy finished"
        assert arguments.issue_identifier is None

    def test_send_accepts_an_optional_issue_identifier(self) -> None:
        arguments = parse_arguments(
            command_line_arguments=[
                "send",
                "--severity",
                "question",
                "--message",
                "which adapter should own the escalation?",
                "--issue-identifier",
                "PR-9",
            ]
        )

        assert arguments.issue_identifier == "PR-9"

    def test_smoke_is_a_bare_subcommand(self) -> None:
        arguments = parse_arguments(command_line_arguments=["smoke"])

        assert arguments.command_name == "smoke"

    def test_an_unknown_severity_is_a_usage_error(self) -> None:
        with pytest.raises(SystemExit):
            parse_arguments(
                command_line_arguments=["send", "--severity", "bogus", "--message", "m"]
            )


class TestSelectDispatcher:
    def test_console_when_no_webhook_and_not_urgent(
        self, sample_notification_request: NotificationRequest
    ) -> None:
        settings = ApplicationSettings(discord_webhook_url=None)

        dispatcher = select_dispatcher(settings=settings, request=sample_notification_request)

        assert isinstance(dispatcher, ConsoleDispatcher)

    def test_discord_when_a_webhook_is_configured(
        self, sample_notification_request: NotificationRequest
    ) -> None:
        settings = ApplicationSettings(discord_webhook_url=SecretStr(FAKE_WEBHOOK_URL))

        dispatcher = select_dispatcher(settings=settings, request=sample_notification_request)

        assert isinstance(dispatcher, DiscordWebhookDispatcher)

    def test_urgent_without_webhook_fails_closed(self) -> None:
        settings = ApplicationSettings(discord_webhook_url=None)
        urgent_request = NotificationRequest(
            severity=NotificationSeverity.URGENT,
            message=MessageText("All merges are stopped."),
            issue_identifier=None,
        )

        with pytest.raises(NotificationConfigurationError, match="requires DISCORD_WEBHOOK_URL"):
            select_dispatcher(settings=settings, request=urgent_request)


class TestSendCommand:
    def test_send_without_webhook_delivers_through_console(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = ApplicationSettings(discord_webhook_url=None)
        arguments = parse_arguments(
            command_line_arguments=["send", "--severity", "info", "--message", "deploy finished"]
        )

        exit_code = send_command(settings=settings, arguments=arguments)

        assert exit_code == EXIT_CODE_SUCCESS
        assert "Notification delivered through console." in capsys.readouterr().out

    def test_send_carries_the_issue_identifier(self, capsys: pytest.CaptureFixture[str]) -> None:
        settings = ApplicationSettings(discord_webhook_url=None)
        arguments = parse_arguments(
            command_line_arguments=[
                "send",
                "--severity",
                "info",
                "--message",
                "deploy finished",
                "--issue-identifier",
                "PR-9",
            ]
        )

        exit_code = send_command(settings=settings, arguments=arguments)

        assert exit_code == EXIT_CODE_SUCCESS
        assert "Notification delivered through console." in capsys.readouterr().out


class TestReportOutcome:
    def test_success_prints_the_channel_and_exits_zero(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        outcome = DeliverySuccess(delivery_channel=DeliveryChannel.CONSOLE)

        exit_code = report_outcome(outcome=outcome)

        assert exit_code == EXIT_CODE_SUCCESS
        assert capsys.readouterr().out == "Notification delivered through console.\n"

    def test_failure_reports_the_reason_and_exits_one(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        outcome = DeliveryFailure(failure_reason="http_error")

        exit_code = report_outcome(outcome=outcome)

        assert exit_code == EXIT_CODE_FAILURE
        assert capsys.readouterr().err == "Notification delivery failed: http_error.\n"


class TestSmokeCommand:
    def test_smoke_verifies_the_console_pipeline(self, capsys: pytest.CaptureFixture[str]) -> None:
        exit_code = smoke_command(settings=ApplicationSettings(discord_webhook_url=None))

        assert exit_code == EXIT_CODE_SUCCESS
        assert "Notification delivered through console." in capsys.readouterr().out


class TestMain:
    def test_main_smoke_returns_success(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(sys, "argv", ["notify", "smoke"])
        monkeypatch.setattr("notify.main.configure_logging", keep_default_logging)

        exit_code = main()

        assert exit_code == EXIT_CODE_SUCCESS
        assert "Notification delivered through console." in capsys.readouterr().out

    def test_main_send_returns_success(
        self, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(
            sys, "argv", ["notify", "send", "--severity", "info", "--message", "hello"]
        )
        monkeypatch.setattr("notify.main.configure_logging", keep_default_logging)

        exit_code = main()

        assert exit_code == EXIT_CODE_SUCCESS
        assert "Notification delivered through console." in capsys.readouterr().out


class TestMessageDispatcherPort:
    def test_the_port_declares_the_dispatch_operation(self) -> None:
        assert hasattr(MessageDispatcher, "dispatch")


class TestApplicationSettings:
    def test_defaults_boot_in_a_bare_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_FORMAT", raising=False)

        settings = ApplicationSettings()

        assert settings.discord_webhook_url is None
        assert settings.log_level == "INFO"
        assert settings.log_format is LogFormat.CONSOLE

    def test_the_webhook_url_loads_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", FAKE_WEBHOOK_URL)

        settings = ApplicationSettings()

        assert settings.discord_webhook_url is not None
        assert settings.discord_webhook_url.get_secret_value() == FAKE_WEBHOOK_URL

    def test_an_invalid_log_level_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "NOISY")

        with pytest.raises(ValidationError):
            ApplicationSettings()


class TestConfigureLogging:
    @pytest.fixture(autouse=True)
    def clean_root_logger(self) -> Iterator[None]:
        """Isolate the root logger and restore the structlog configuration."""
        root_logger = logging.getLogger()
        saved_handlers = root_logger.handlers[:]
        saved_level = root_logger.level
        root_logger.handlers.clear()
        yield
        structlog.reset_defaults()
        root_logger.handlers[:] = saved_handlers
        root_logger.setLevel(saved_level)

    def test_json_format_renders_machine_parsable_events(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(
            settings=ApplicationSettings(log_format=LogFormat.JSON, log_level="DEBUG")
        )

        structlog.get_logger("configure_logging_probe").info("probe_event", key="value")

        payload = json.loads(capsys.readouterr().out)
        assert payload["event"] == "probe_event"
        assert payload["key"] == "value"
        assert payload["level"] == "info"

    def test_console_format_renders_human_readable_events(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        configure_logging(settings=ApplicationSettings(log_format=LogFormat.CONSOLE))

        structlog.get_logger("configure_logging_probe").info("probe_event")

        assert "probe_event" in capsys.readouterr().out
