"""Composition root: CLI entry point of the ``notify`` package.

This is the ONLY module where logging is configured (ADR-0005) and where
adapters are constructed (ADR-0002). ``print`` is allowed here and nowhere
else: a CLI speaks to its user on stdout, everything else speaks in logs.
"""

import argparse
import logging
import sys
from collections.abc import Sequence

import httpx
import structlog
from structlog.dev import ConsoleRenderer
from structlog.processors import (
    add_log_level,
    add_logger_name,
    merge_contextvars,
    JSONRenderer,
    TimeStamper,
)
from structlog.stdlib import ProcessorFormatter
from structlog.typing import Processor

from notify.infrastructure.notifications.console import ConsoleDispatcher
from notify.infrastructure.notifications.discord import DiscordWebhookDispatcher
from notify.models.errors import NotificationConfigurationError
from notify.models.notification import (
    NotificationOutcome,
    NotificationRequest,
    NotificationSeverity,
)
from notify.models.settings import ApplicationSettings, LogFormat
from notify.models.types import IssueIdentifier, MessageText
from notify.services.notifications import dispatch_notification, is_urgent
from notify.services.ports import MessageDispatcher

logger = structlog.get_logger(__name__)

EXIT_CODE_SUCCESS = 0
EXIT_CODE_FAILURE = 1

SMOKE_TEST_MESSAGE_TEXT = (
    "notify smoke test: the notification pipeline is alive."
)


def configure_logging(*, settings: ApplicationSettings) -> None:
    """Wire structlog to the stdlib root logger.

    Must stay in this module: configuring logging anywhere else would make
    the log format depend on import order, which is untestable and unfair to
    both human readers and machine parsers.
    """
    shared_processors: list[Processor] = [
        merge_contextvars,
        add_log_level,
        add_logger_name,
        TimeStamper(fmt="iso"),
    ]
    logging.basicConfig(stream=sys.stdout, level=settings.log_level)
    if settings.log_format is LogFormat.JSON:
        event_renderer: Processor = JSONRenderer()
    else:
        event_renderer = ConsoleRenderer()
    root_handler = logging.getLogger().handlers[0]
    formatter = ProcessorFormatter(
        processors=[
            ProcessorFormatter.remove_processors_meta,
            event_renderer,
        ],
        foreign_pre_chain=shared_processors,
    )
    root_handler.setFormatter(formatter)
    structlog.configure(
        processors=shared_processors
        + [
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def parse_arguments(*, command_line_arguments: Sequence[str]) -> argparse.Namespace:
    """Parse the CLI contract: subcommands ``send`` and ``smoke``."""
    argument_parser = argparse.ArgumentParser(
        prog="notify",
        description="Notify the project owner through the configured channel.",
    )
    subparsers = argument_parser.add_subparsers(dest="command_name", required=True)

    send_parser = subparsers.add_parser(
        "send",
        help="Send one notification to the project owner.",
    )
    send_parser.add_argument(
        "--severity",
        required=True,
        choices=[severity.value for severity in NotificationSeverity],
        help="How urgent the notification is.",
    )
    send_parser.add_argument(
        "--message",
        required=True,
        help="The notification message body.",
    )
    send_parser.add_argument(
        "--issue-identifier",
        required=False,
        default=None,
        help="The issue this notification relates to, if any.",
    )

    subparsers.add_parser(
        "smoke",
        help="Run one console-delivered notification to verify the pipeline.",
    )

    return argument_parser.parse_args(command_line_arguments)


def select_dispatcher(
    *,
    settings: ApplicationSettings,
    request: NotificationRequest,
) -> MessageDispatcher:
    """Choose the delivery adapter from settings and request urgency.

    Urgent requests must reach the human owner on a push channel: degrading
    them to the console silently would be a lie, so we raise instead.
    """
    if settings.discord_webhook_url is None:
        if is_urgent(request):
            raise NotificationConfigurationError(
                "An urgent notification requires DISCORD_WEBHOOK_URL to be configured."
            )
        logger.debug("console_dispatcher_selected")
        return ConsoleDispatcher()
    logger.debug("discord_dispatcher_selected")
    # Narrowing assertion for mypy: the None case returned above.
    # noqa: S101
    assert settings.discord_webhook_url is not None
    return DiscordWebhookDispatcher(
        webhook_url=settings.discord_webhook_url,
        http_client=httpx.Client(),
    )


def send_command(
    *, settings: ApplicationSettings, arguments: argparse.Namespace
) -> int:
    """Handle the ``send`` subcommand."""
    issue_identifier = (
        IssueIdentifier(arguments.issue_identifier)
        if arguments.issue_identifier is not None
        else None
    )
    request = NotificationRequest(
        severity=NotificationSeverity(arguments.severity),
        message=MessageText(arguments.message),
        issue_identifier=issue_identifier,
    )
    dispatcher = select_dispatcher(settings=settings, request=request)
    outcome = dispatch_notification(request=request, dispatcher=dispatcher)
    return report_outcome(outcome=outcome)


def report_outcome(*, outcome: NotificationOutcome) -> int:
    """Print the machine-checkable outcome and translate it to an exit code."""
    if outcome.kind == "success":
        print(f"Notification delivered through {outcome.delivery_channel.value}.")
        return EXIT_CODE_SUCCESS
    logger.error("send_command_failed", failure_reason=outcome.failure_reason)
    print(f"Notification delivery failed: {outcome.failure_reason}.", file=sys.stderr)
    return EXIT_CODE_FAILURE


def smoke_command(*, settings: ApplicationSettings) -> int:
    """Handle the ``smoke`` subcommand: console delivery of a fixed message."""
    request = NotificationRequest(
        severity=NotificationSeverity.INFO,
        message=MessageText(SMOKE_TEST_MESSAGE_TEXT),
        issue_identifier=None,
    )
    dispatcher = ConsoleDispatcher()
    outcome = dispatch_notification(request=request, dispatcher=dispatcher)
    return report_outcome(outcome=outcome)


def main() -> int:
    """Application entry point: load settings, configure, dispatch."""
    settings = ApplicationSettings()
    configure_logging(settings=settings)
    arguments = parse_arguments(command_line_arguments=sys.argv[1:])
    if arguments.command_name == "send":
        return send_command(settings=settings, arguments=arguments)
    if arguments.command_name == "smoke":
        return smoke_command(settings=settings)
    raise AssertionError(f"Unreachable: unknown command {arguments.command_name}.")


if __name__ == "__main__":
    sys.exit(main())
