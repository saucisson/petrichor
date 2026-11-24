"""Usage-to-model routing with pinned exact versions (P74, ADR-0108).

One model per LLM usage, acté par ADR-0108 and motivated by annexe B §B.4
of the 2026-11-20/21 directive: ``semantic-profile`` and ``cluster`` run on
Mistral Large 4, ``reading`` and ``clarification`` on Mistral Small 4,
``narration`` on Ministral 3 14B with a Small 4 fallback. Every version is
PINNED in this versioned configuration — the ``-latest`` aliases are
forbidden (replay P46: an alias moves without notice and breaks replays),
and the guard test rejects any of them with an explicit error.

The routing stays behind :class:`~petrichor.llm.provider.LLMProvider`: no
vendor lock-in, the deterministic mock is unchanged and remains the default
provider of the tests.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_validator

from petrichor.llm.provider import LLMError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from petrichor.llm.schemas import LlmUsage

FORBIDDEN_ALIAS_SUFFIX = "-latest"
"""The mutable alias suffix forbidden in every pinned identifier (replay P46)."""


class RoutingError(LLMError):
    """Explicit error: the routing configuration carries a forbidden alias or misses a usage."""


class ModelRoute(BaseModel):
    """A pinned route: the exact model id sent to the API and its version fingerprint."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    model: str
    """The exact pinned model identifier sent in the request body (never a ``-latest`` alias)."""
    version: str
    """The exact pinned version recorded in the archive fingerprint (replay P46)."""
    fallback: "ModelRoute | None" = None
    """The pinned fallback route used when the primary model degrades (annexe B §B.4)."""

    @field_validator("model", "version")
    @classmethod
    def _no_latest_alias(cls, value: str) -> str:
        if FORBIDDEN_ALIAS_SUFFIX in value:
            raise ValueError(
                f"Forbidden mutable alias {FORBIDDEN_ALIAS_SUFFIX!r} in pinned "
                f"identifier {value!r}: pin an exact version (ADR-0108, replay P46)."
            )
        return value


ModelRoute.model_rebuild()

USAGE_MODEL_ROUTING: dict[LlmUsage, ModelRoute] = {
    "semantic-profile": ModelRoute(
        model="mistral-large-4-2510",
        version="mistral-large-4-2510",
    ),
    "reading": ModelRoute(
        model="mistral-small-2603",
        version="mistral-small-2603",
    ),
    "clarification": ModelRoute(
        model="mistral-small-2603",
        version="mistral-small-2603",
    ),
    "cluster": ModelRoute(
        model="mistral-large-4-2510",
        version="mistral-large-4-2510",
    ),
    "narration": ModelRoute(
        model="ministral-14b-2512",
        version="ministral-14b-2512",
        fallback=ModelRoute(
            model="mistral-small-2603",
            version="mistral-small-2603",
        ),
    ),
}
"""The versioned routing table {usage: {model, version}} (ADR-0108, annexe B §B.4).

Exact identifiers pinned on 2026-11-21 against the public Mistral catalog
(``mistral-small-2603``, ``ministral-14b-2512`` confirmed; the Large 4
suffix follows the catalog YYMM convention of its 2026-10 public preview);
re-confirm against ``GET /v1/models`` at deploy time.
"""

ALL_USAGES: tuple[LlmUsage, ...] = (
    "semantic-profile",
    "reading",
    "clarification",
    "cluster",
    "narration",
)
"""Every usage of the LLM layer, in a fixed order (exhaustiveness guard)."""


def validate_routing(routing: Mapping[LlmUsage, ModelRoute]) -> None:
    """Reject explicitly any routing that misses a usage or carries a ``-latest`` alias."""
    missing = [usage for usage in ALL_USAGES if usage not in routing]
    if missing:
        raise RoutingError(f"Routing misses the usages: {missing}.")
    for usage in ALL_USAGES:
        route = routing[usage]
        for identifier in (route.model, route.version):
            if FORBIDDEN_ALIAS_SUFFIX in identifier:
                raise RoutingError(
                    f"Forbidden mutable alias {FORBIDDEN_ALIAS_SUFFIX!r} in the pinned identifier "
                    f"{identifier!r} of usage {usage!r} (ADR-0108, replay P46)."
                )


def route_for(routing: Mapping[LlmUsage, ModelRoute], *, usage: LlmUsage) -> ModelRoute:
    """Return the pinned route of a usage; the mapping is validated, so the key exists."""
    validate_routing(routing)
    return routing[usage]


__all__ = [
    "ALL_USAGES",
    "FORBIDDEN_ALIAS_SUFFIX",
    "USAGE_MODEL_ROUTING",
    "ModelRoute",
    "RoutingError",
    "route_for",
    "validate_routing",
]
