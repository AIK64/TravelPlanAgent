from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from travel_agent.domain.critique_models import SoftCriticRequest
from travel_agent.domain.models import PlanCandidate, PlanningPOI, TripSpec
from travel_agent.domain.repair_models import CriticReport, RepairAttempt
from travel_agent.planning.drafts import CandidateDraft


class PlannerContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trip: TripSpec
    poi_query_limit: int = Field(ge=1, le=100)
    max_queries: int = Field(ge=1, le=100)


class CriticContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    request: SoftCriticRequest
    candidate_ids: tuple[str, ...]


class ReplannerContext(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    trip: TripSpec
    candidate: PlanCandidate
    draft: CandidateDraft
    planning_pois: tuple[PlanningPOI, ...]
    critic_report: CriticReport
    repair_round: int = Field(ge=1)
    previous_action_fingerprints: frozenset[str] = frozenset()
