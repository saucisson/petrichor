"""Domain primitives: named types that make boundaries self-documenting.

A bare ``str`` at a domain boundary is a review finding (ADR-0006): these
NewTypes give every string a name the type checker can enforce.
"""

from typing import NewType

IssueIdentifier = NewType("IssueIdentifier", str)
"""A platform issue reference, e.g. ``PR-42``."""

MessageText = NewType("MessageText", str)
"""A human-readable notification body."""

WebhookUrl = NewType("WebhookUrl", str)
"""A full https URL of an incoming webhook."""

type JsonValue = None | bool | int | float | str | list[JsonValue] | dict[str, JsonValue]
"""Any JSON-compatible value (PEP 695 recursive alias)."""
