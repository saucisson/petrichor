"""Static/MC triage of check questions and non-vacuity guards (spec §14.2, §14.4; issue #14).

Every question carries a triage ``statique | MC | dégradé`` with a
justification, computed from its pattern family and the current capacity of
the embedded engine (spec §14.2): a question is static only when it reduces
to a linear invariant, a conservation equation or a local structural
property, and only while the engine's capacity covers the model. When the
static proof cannot be found, the question is explicitly degraded - never a
silent verdict - and the degradation is displayed on the question state.

The verdict lock (spec §19.4) forbids any module outside ``petrichor.engines``
from importing that package, so the capacity probe, the static resolution and
the non-vacuity guard reach the API through the injected ``StaticResolver``
protocol; the concrete adapter lives in ``engines/mini_engine.py`` and
returns plain data, never a verdict object.
"""

from typing import TYPE_CHECKING, Literal, Protocol

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.checks import CheckFamilyName, CheckQuestion, KindPattern, ToolVersion

if TYPE_CHECKING:
    from petrichor.petri_canonical import CanonicalModel, JsonValue

logger = structlog.get_logger(__name__)

type TriageKind = Literal["statique", "MC", "dégradé"]
"""The triage of a question: static, model-checked, or explicitly degraded."""

STATIC_KINDS: frozenset[str] = frozenset(
    {"safety.invariant", "safety.conservation", "safety.state_invariant"}
)
"""The patterns reducible to the three static targets of spec §14.2."""

STATIC_TOOL_VERSION = ToolVersion("mini-engine")
"""The tool version archived for runs resolved by the embedded static engine."""

STATIC_COST_SECONDS = 0.0
"""A static resolution consumes no tool time (spec §18.1, coût nul)."""


class CapacityReport(BaseModel):
    """The current capacity of the embedded engine over one model (plain data)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    place_count: int = Field(ge=0)
    max_places_for_invariants: int = Field(gt=0)
    state_count: int | None = Field(default=None, ge=1)
    """The reachable state count, or None when the net is beyond the state cap."""

    max_states: int = Field(gt=0)

    @property
    def within_invariant_capacity(self) -> bool:
        """Whether the invariant enumeration covers the model's places."""
        return self.place_count <= self.max_places_for_invariants

    @property
    def within_state_capacity(self) -> bool:
        """Whether the reachability graph fits the state cap."""
        return self.state_count is not None

    @property
    def within_static_capacity(self) -> bool:
        """Whether the embedded engine can decide a static question on this model."""
        return self.within_invariant_capacity and self.within_state_capacity


class QuestionTriage(BaseModel):
    """The triage of one question, with the justification the front displays."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: TriageKind
    justification: str = Field(min_length=1)


def triage_question(*, kind: KindPattern, capacity: CapacityReport | None) -> QuestionTriage:
    """Compute the triage of a question from its pattern and the current capacity.

    Spec §14.2: static only for the three static targets, and only within
    capacity; a static pattern beyond capacity is explicitly degraded to MC
    with the motif "preuve statique non trouvée" - the failure of a heuristic
    is never read as a verdict.
    """
    if kind not in STATIC_KINDS:
        return QuestionTriage(
            kind="MC",
            justification=(
                f"Pattern {kind!r} hors des trois décibles statiques"
                " (invariant linéaire, équation de conservation, propriété structurelle locale)"
                " : run MC (spec §14.2)."
            ),
        )
    if capacity is None:
        return QuestionTriage(
            kind="dégradé",
            justification=(
                "Capacité courante inconnue (aucune sonde câblée) : preuve statique non trouvée,"
                " question déclassée MC (spec §14.2)."
            ),
        )
    if not capacity.within_static_capacity:
        state = "hors cap" if capacity.state_count is None else f"{capacity.state_count} états"
        return QuestionTriage(
            kind="dégradé",
            justification=(
                f"Preuve statique non trouvée : {capacity.place_count} places"
                f" (cap invariants {capacity.max_places_for_invariants}), {state}"
                f" (cap {capacity.max_states}) — question déclassée MC (spec §14.2)."
            ),
        )
    return QuestionTriage(
        kind="statique",
        justification=(
            f"Réductible à un invariant linéaire / une conservation (spec §14.2), décidable par le"
            f" mini-moteur : {capacity.place_count} places ≤ cap"
            f" {capacity.max_places_for_invariants}, {capacity.state_count} états ≤ cap"
            f" {capacity.max_states}."
        ),
    )


type GuardKind = Literal["ef_phi", "ag_psi"]
"""The non-vacuity guard of a question: ``EF φ`` or ``AG ψ`` (spec §14.4)."""

type NonVacuityStatus = Literal["ok", "vacuous", "not_applicable"]
"""The guard status: satisfied, vacuous (the initial marking already decides), or no guard."""


class NonVacuityGuard(BaseModel):
    """The non-vacuity chip of one question (spec §14.4, maquette P8).

    For ``EF φ`` the guard is ok when the initial marking does not already
    satisfy φ; for ``AG ψ`` when it does not violate ψ. A vacuous guard means
    the property says nothing about this model: no verdict is rendered on a
    mute property.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    guard: GuardKind | None = None
    status: NonVacuityStatus
    detail: str = Field(min_length=1)


class StaticUndecidableError(Exception):
    """The embedded engine cannot decide the question statically within its capacity."""


class StaticResolver(Protocol):
    """The injected window on the embedded static engine (spec §19.4 verdict lock).

    Implementations live in ``petrichor.engines`` and return plain data: the
    API never interprets a verdict, it stores and serves the payload verbatim.
    """

    def capacity(self, *, model: CanonicalModel) -> CapacityReport:
        """Probe the current capacity of the engine over the model."""
        ...

    def resolve_static(
        self, *, model: CanonicalModel, question: CheckQuestion
    ) -> dict[str, JsonValue]:
        """Resolve a statically-triaged question; raise ``StaticUndecidableError`` past capacity."""
        ...

    def non_vacuity_guard(
        self, *, model: CanonicalModel, family: CheckFamilyName, kind: KindPattern, formula: str
    ) -> NonVacuityGuard:
        """Compute the non-vacuity guard of a question on the model's initial marking."""
        ...
