"""Shared pytest fixtures for the whole test suite."""

from collections.abc import Iterator
from typing import Any

import pytest
import structlog.testing

from notify.models.notification import NotificationRequest, NotificationSeverity
from notify.models.types import IssueIdentifier, MessageText


@pytest.fixture
def sample_notification_request() -> NotificationRequest:
    """A typical agent-to-human question, as used across the test suite."""
    return NotificationRequest(
        severity=NotificationSeverity.QUESTION,
        message=MessageText(
            "The reviewer agent requests a decision on the persistence adapter."
        ),
        issue_identifier=IssueIdentifier("PR-42"),
    )


@pytest.fixture
def capture_logs() -> Iterator[list[dict[str, Any]]]:
    """Capture structlog events, as a list of plain dictionaries."""
    with structlog.testing.capture_logs() as captured_events:
        yield captured_events
