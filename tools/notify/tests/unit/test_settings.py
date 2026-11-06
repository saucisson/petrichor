"""Unit tests for environment-driven settings (ADR-0009: secrets stay SecretStr)."""

import pytest
from pydantic import SecretStr, ValidationError

from notify.models.settings import ApplicationSettings, LogFormat


class TestDefaults:
    def test_barest_environment_starts_with_console_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("LOG_LEVEL", raising=False)
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        settings = ApplicationSettings()
        assert settings.discord_webhook_url is None
        assert settings.log_level == "INFO"
        assert settings.log_format is LogFormat.CONSOLE


class TestEnvironmentOverrides:
    def test_webhook_url_arrives_as_secret(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/1/t")
        settings = ApplicationSettings()
        assert isinstance(settings.discord_webhook_url, SecretStr)
        assert (
            settings.discord_webhook_url.get_secret_value()
            == "https://discord.com/api/webhooks/1/t"
        )

    def test_log_level_and_format_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("LOG_FORMAT", "json")
        settings = ApplicationSettings()
        assert settings.log_level == "DEBUG"
        assert settings.log_format is LogFormat.JSON

    def test_invalid_log_level_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "CHAT")
        with pytest.raises(ValidationError):
            ApplicationSettings()
