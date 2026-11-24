"""Typed domain errors.

Errors are data too (ADR-0002): they carry a human-readable message and
nothing else - callers decide what to log or report.
"""


class NotifyError(Exception):
    """Base class of every domain error raised by this application."""


class NotificationConfigurationError(NotifyError):
    """A notification was requested that the current settings cannot serve."""
