"""The notification domain language.

Severity is a closed set with behavior (ADR-0006): every switch over it
must be exhaustive, enforced by ``match`` + ``assert_never``. Outcomes are
a discriminated union so ``kind`` narrows statically.
"""

from enum import StrEnum
from typing import Annotated, Literal, assert_never

from pydantic import BaseModel, ConfigDict, Field

from notify.models.types import IssueIdentifier, MessageText

MAXIMUM_MESSAGE_TEXT_LENGTH = 2000
MAXIMUM_EMBED_TITLE_LENGTH = 256
MAXIMUM_EMBED_FIELD_COUNT = 25


class NotificationSeverity(StrEnum):
    """How urgent a notification is (escalation contract, ADR-0012)."""

    QUESTION = "question"
    URGENT = "urgent"
    INFO = "info"

    @property
    def display_emoji(self) -> str:
        """One emoji per severity, used in embed titles and content."""
        match self:
            case NotificationSeverity.QUESTION:
                return "🟡"
            case NotificationSeverity.URGENT:
                return "🔴"
            case NotificationSeverity.INFO:
                return "🔵"
            case unreachable:
                assert_never(unreachable)

    @property
    def display_label(self) -> str:
        """The human-facing severity word, used in embed titles."""
        match self:
            case NotificationSeverity.QUESTION:
                return "Question"
            case NotificationSeverity.URGENT:
                return "Urgent"
            case NotificationSeverity.INFO:
                return "Info"
            case unreachable:
                assert_never(unreachable)


class DeliveryChannel(StrEnum):
    """The adapter that carried a notification to the owner."""

    CONSOLE = "console"
    DISCORD = "discord"


class NotificationRequest(BaseModel):
    """One agent-to-human notification, before delivery."""

    model_config = ConfigDict(frozen=True)

    severity: NotificationSeverity
    message: MessageText = Field(max_length=MAXIMUM_MESSAGE_TEXT_LENGTH)
    issue_identifier: IssueIdentifier | None = None


class DiscordEmbedField(BaseModel):
    """One name/value pair rendered inside a Discord embed."""

    model_config = ConfigDict(frozen=True)

    name: str
    value: str


class DiscordEmbed(BaseModel):
    """The rich block of a Discord message."""

    model_config = ConfigDict(frozen=True)

    title: str = Field(max_length=MAXIMUM_EMBED_TITLE_LENGTH)
    description: str
    color: int
    fields: tuple[DiscordEmbedField, ...] = ()


class DiscordMessage(BaseModel):
    """The exact payload posted to a Discord webhook."""

    model_config = ConfigDict(frozen=True)

    content: str
    embeds: tuple[DiscordEmbed, ...] = Field(max_length=1)


class DeliverySuccess(BaseModel):
    """A notification reached a real channel."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["success"] = "success"
    delivery_channel: DeliveryChannel


class DeliveryFailure(BaseModel):
    """A notification did not reach any channel, with a machine reason."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["failure"] = "failure"
    failure_reason: Literal["http_error"]


NotificationOutcome = Annotated[
    DeliverySuccess | DeliveryFailure,
    Field(discriminator="kind"),
]
