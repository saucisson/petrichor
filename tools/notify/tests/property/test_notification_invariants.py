"""Property tests for notification invariants (ADR-0013).

Hypothesis verifies invariants that hold for *every* input, not just the
cases a human thought of - a counterexample-seeking reviewer in test form.
"""

import pytest
from hypothesis import given, settings as hypothesis_settings
from hypothesis import strategies as st

from notify.models.notification import (
    MAXIMUM_MESSAGE_TEXT_LENGTH,
    NotificationRequest,
    NotificationSeverity,
)
from notify.models.types import MessageText
from notify.services.notifications import build_discord_message

pytestmark = pytest.mark.property

MAXIMUM_EMBED_COLOR = 0xFFFFFF

message_text_strategy = st.text(min_size=1, max_size=MAXIMUM_MESSAGE_TEXT_LENGTH)
severity_strategy = st.sampled_from(NotificationSeverity)


@hypothesis_settings(max_examples=200)
@given(message_text=message_text_strategy, severity=severity_strategy)
def test_embed_invariants_hold_for_any_message(
    message_text: str, severity: NotificationSeverity
) -> None:
    """Whatever the message and severity, the Discord payload stays valid."""
    request = NotificationRequest(
        severity=severity,
        message=MessageText(message_text),
        issue_identifier=None,
    )

    discord_message = build_discord_message(request=request)

    assert len(discord_message.embeds) == 1
    embed = discord_message.embeds[0]
    assert severity.display_label in embed.title
    assert embed.description == message_text
    assert len(embed.description) > 0
    assert 0 <= embed.color <= MAXIMUM_EMBED_COLOR
    assert len(discord_message.content) > 0
