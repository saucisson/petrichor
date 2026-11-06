"""Unit tests for the typed domain errors (ADR-0003: errors are data)."""

import pytest

from notify.models.errors import NotificationConfigurationError, NotifyError


class TestDomainErrors:
    def test_configuration_error_is_a_notify_error(self) -> None:
        assert issubclass(NotificationConfigurationError, NotifyError)

    def test_error_carries_its_message(self) -> None:
        with pytest.raises(NotifyError, match="missing webhook"):
            raise NotificationConfigurationError("missing webhook")
