"""Unit tests for the composition root: CLI parsing, dispatch, exit codes."""

import sys

import pytest
import structlog
from pydantic import SecretStr

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


def make_settings(**overrides: object) -> ApplicationSettings:
    """Build settings with test-friendly defaults, overridable per test."""
    return ApplicationSettings(**overrides)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def isolate_structlog_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    """configure_logging mutates global structlog state; neutralize the mutation.

    The body of configure_logging still runs (and is covered); only the
    global structlog.configure call is stubbed so capture_logs fixtures in
    other test modules keep working.
    """
    monkeypatch.setattr(structlog, "configure", lambda **kwargs: None)


class TestParseArguments:
    def test_send_requires_severity_and_message(self) -> None:
        arguments = parse_arguments(
            command_line_arguments=["send", "--severity", "info", "--message", "hello"]
        )
        assert arguments.command_name == "send"
        assert arguments.severity == "info"
        assert arguments.message == "hello"
        assert arguments.issue_identifier is None

    def test_send_accepts_issue_identifier(self) -> None:
        arguments = parse_arguments(
            command_line_arguments=[
                "send",
                "--severity",
                "urgent",
                "--message",
                "stop",
                "--issue-identifier",
                "PR-42",
            ]
        )
        assert arguments.issue_identifier == "PR-42"

    def test_smoke_takes_no_arguments(self) -> None:
        arguments = parse_arguments(command_line_arguments=["smoke"])
        assert arguments.command_name == "smoke"

    def test_unknown_severity_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            parse_arguments(
                command_line_arguments=["send", "--severity", "chat", "--message", "hi"]
            )


class TestSelectDispatcher:
    def test_console_fallback_without_webhook(self) -> None:
        settings = make_settings()
        request = NotificationRequest(
            severity=NotificationSeverity.INFO,
            message=MessageText("hello"),
            issue_identifier=None,
        )
        dispatcher = select_dispatcher(settings=settings, request=request)
        assert isinstance(dispatcher, ConsoleDispatcher)

    def test_urgent_without_webhook_raises(self) -> None:
        settings = make_settings()
        request = NotificationRequest(
            severity=NotificationSeverity.URGENT,
            message=MessageText("all merges stopped"),
            issue_identifier=None,
        )
        with pytest.raises(NotificationConfigurationError, match="DISCORD_WEBHOOK_URL"):
            select_dispatcher(settings=settings, request=request)

    def test_discord_dispatcher_with_webhook(self) -> None:
        settings = make_settings(
            discord_webhook_url=SecretStr("https://discord.com/api/webhooks/1/token")
        )
        request = NotificationRequest(
            severity=NotificationSeverity.INFO,
            message=MessageText("hello"),
            issue_identifier=None,
        )
        dispatcher = select_dispatcher(settings=settings, request=request)
        assert isinstance(dispatcher, DiscordWebhookDispatcher)


class TestCommands:
    def test_send_command_delivers_through_console(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        settings = make_settings()
        arguments = parse_arguments(
            command_line_arguments=["send", "--severity", "info", "--message", "hello"]
        )
        exit_code = send_command(settings=settings, arguments=arguments)
        assert exit_code == EXIT_CODE_SUCCESS
        assert "console" in capsys.readouterr().out

    def test_smoke_command_succeeds(self, capsys: pytest.CaptureFixture[str]) -> None:
        settings = make_settings()
        exit_code = smoke_command(settings=settings)
        assert exit_code == EXIT_CODE_SUCCESS
        assert "delivered" in capsys.readouterr().out

    def test_report_outcome_failure_returns_failure_code(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        outcome = DeliveryFailure(failure_reason="http_error")
        exit_code = report_outcome(outcome=outcome)
        assert exit_code == EXIT_CODE_FAILURE
        assert "http_error" in capsys.readouterr().err

    def test_report_outcome_success_returns_success_code(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        outcome = DeliverySuccess(delivery_channel=DeliveryChannel.CONSOLE)
        exit_code = report_outcome(outcome=outcome)
        assert exit_code == EXIT_CODE_SUCCESS
        assert "console" in capsys.readouterr().out


class TestMain:
    def test_main_smoke(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(sys, "argv", ["notify", "smoke"])
        assert main() == EXIT_CODE_SUCCESS

    def test_main_send(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(
            sys,
            "argv",
            ["notify", "send", "--severity", "question", "--message", "hi"],
        )
        assert main() == EXIT_CODE_SUCCESS


class TestConfigureLogging:
    def test_console_format(self) -> None:
        configure_logging(settings=make_settings())

    def test_json_format(self) -> None:
        configure_logging(settings=make_settings(log_format=LogFormat.JSON))
