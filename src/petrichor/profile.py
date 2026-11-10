"""Semantic profile arbitration and coverage groups (spec §14, issue #12).

The semantic profile (roles) is **proposed** by the LLM and **arbitrated**
by the human: no role is applied without an explicit arbitration. Coverage
groups derive from the arbitrated profile (one sub-role per group); groups
not covered by any arbitrated question are exposed as re-seeding targets.
"""

import threading
from typing import NewType

import structlog
from pydantic import BaseModel, ConfigDict, Field

from petrichor.llm.schemas import SemanticProfile

logger = structlog.get_logger(__name__)

ArbitrationId = NewType("ArbitrationId", str)
"""An arbitration identifier, e.g. ``arb_7c2a9f``."""

ARBITRATION_ID_PREFIX = "arb_"
ARBITRATION_ID_HEX_DIGITS = 6


class ArbitrationDecision(BaseModel):
    """One human arbitration of a proposed role (spec §14)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry_id: str
    accepted: bool
    chosen_role: str = Field(min_length=1)
    """The role the human chose; equals the proposed role when accepted."""


class CoverageGroup(BaseModel):
    """One coverage group derived from an arbitrated role (spec §14)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_id: str
    role: str
    elements: str
    covered_by: tuple[str, ...] = ()
    """The arbitrated questions covering this group, by their kind."""


class ProfileState(BaseModel):
    """The full profile state of a session: proposed + arbitrated + groups."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposed: SemanticProfile
    arbitrations: tuple[ArbitrationDecision, ...] = ()
    groups: tuple[CoverageGroup, ...] = ()

    def is_arbitrated(self, *, entry_id: str) -> bool:
        """Whether a proposed entry has been arbitrated by the human."""
        return any(a.entry_id == entry_id for a in self.arbitrations)

    def uncovered_groups(self) -> tuple[CoverageGroup, ...]:
        """The groups not covered by any arbitrated question (re-seeding targets)."""
        return tuple(g for g in self.groups if not g.covered_by)


class ProfileArbitrator:
    """Holds the profile state of sessions and applies human arbitrations.

    Thread-safe; no role is ever applied without an explicit arbitration
    (spec §14): the arbitrated profile contains only accepted entries.
    """

    def __init__(self) -> None:
        """Start with no profile state."""
        self._lock = threading.Lock()
        self._states: dict[str, ProfileState] = {}

    def propose(self, *, session_id: str, profile: SemanticProfile) -> ProfileState:
        """Register a proposed profile for a session; groups derived from entries."""
        groups = tuple(
            CoverageGroup(
                group_id=f"grp_{entry.id}",
                role=entry.role,
                elements=entry.elements,
            )
            for entry in profile.entries
        )
        state = ProfileState(proposed=profile, arbitrations=(), groups=groups)
        with self._lock:
            self._states[session_id] = state
        logger.debug(
            "profile_proposed",
            session_id=session_id,
            entries=len(profile.entries),
            groups=len(groups),
        )
        return state

    def arbitrate(self, *, session_id: str, decision: ArbitrationDecision) -> ProfileState | None:
        """Apply one human arbitration; None if the session has no proposed profile."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return None
            if state.is_arbitrated(entry_id=decision.entry_id):
                logger.warning(
                    "profile_already_arbitrated", session_id=session_id, entry_id=decision.entry_id
                )
                return state
            new_arbitrations = (*state.arbitrations, decision)
            # Rebuild groups: only arbitrated+accepted entries produce active groups
            accepted_ids = {a.entry_id for a in new_arbitrations if a.accepted}
            new_groups = tuple(
                g.model_copy(
                    update={
                        "role": next(
                            a.chosen_role
                            for a in new_arbitrations
                            if a.entry_id == g.group_id.removeprefix("grp_")
                        )
                    }
                )
                for g in state.groups
                if g.group_id.removeprefix("grp_") in accepted_ids
            )
            updated = ProfileState(
                proposed=state.proposed,
                arbitrations=new_arbitrations,
                groups=new_groups,
            )
            self._states[session_id] = updated
        logger.debug(
            "profile_arbitrated",
            session_id=session_id,
            entry_id=decision.entry_id,
            accepted=decision.accepted,
        )
        return updated

    def get(self, *, session_id: str) -> ProfileState | None:
        """Return the profile state of a session, or None."""
        with self._lock:
            return self._states.get(session_id)

    def mark_covered(self, *, session_id: str, group_id: str, question_kind: str) -> None:
        """Mark a coverage group as covered by an arbitrated question."""
        with self._lock:
            state = self._states.get(session_id)
            if state is None:
                return
            new_groups = tuple(
                g.model_copy(update={"covered_by": (*g.covered_by, question_kind)})
                if g.group_id == group_id
                else g
                for g in state.groups
            )
            self._states[session_id] = state.model_copy(update={"groups": new_groups})
        logger.debug(
            "profile_group_covered",
            session_id=session_id,
            group_id=group_id,
            kind=question_kind,
        )
