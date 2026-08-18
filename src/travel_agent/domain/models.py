from __future__ import annotations

from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, Field, model_validator


class Pace(StrEnum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    INTENSIVE = "intensive"


class PlanStyle(StrEnum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    EXPLORATION = "exploration"


class ItemType(StrEnum):
    ACTIVITY = "activity"
    MEAL = "meal"
    REST = "rest"
    TRANSPORT = "transport"


class ViolationSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


class Coordinate(BaseModel):
    longitude: float = Field(ge=-180, le=180)
    latitude: float = Field(ge=-90, le=90)


class TimeWindow(BaseModel):
    start: time
    end: time

    @model_validator(mode="after")
    def validate_order(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError("time window end must be later than start")
        return self


class TransportAnchor(BaseModel):
    name: str
    at: datetime
    coordinate: Coordinate

    @model_validator(mode="after")
    def require_timezone(self) -> "TransportAnchor":
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("transport anchor datetime must be timezone-aware")
        return self


class LocationAnchor(BaseModel):
    name: str
    coordinate: Coordinate


class MobilityConstraints(BaseModel):
    max_daily_walking_meters: int = Field(default=8_000, ge=0)
    max_daily_activity_minutes: int = Field(default=480, gt=0)
    needs_frequent_rest: bool = False


class TripSpec(BaseModel):
    destination: str = Field(min_length=1)
    start_date: date
    end_date: date
    travelers: int = Field(default=1, ge=1, le=20)
    arrival: TransportAnchor
    departure: TransportAnchor
    accommodation: LocationAnchor | None = None
    total_budget: Decimal | None = Field(default=None, gt=0)
    interests: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    must_visit: list[str] = Field(default_factory=list)
    pace: Pace = Pace.BALANCED
    mobility: MobilityConstraints = Field(default_factory=MobilityConstraints)
    daily_start: time = time(9, 0)
    daily_end: time = time(20, 0)

    @model_validator(mode="after")
    def validate_trip_dates(self) -> "TripSpec":
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be earlier than start_date")
        if self.arrival.at.date() < self.start_date:
            raise ValueError("arrival must not be earlier than start_date")
        if self.departure.at.date() > self.end_date:
            raise ValueError("departure must not be later than end_date")
        if self.departure.at <= self.arrival.at:
            raise ValueError("departure must be later than arrival")
        if self.daily_end <= self.daily_start:
            raise ValueError("daily_end must be later than daily_start")
        return self

    @property
    def day_count(self) -> int:
        return (self.end_date - self.start_date).days + 1


class POI(BaseModel):
    id: str
    name: str
    city: str
    coordinate: Coordinate
    categories: list[str]
    opening_window: TimeWindow
    estimated_duration_minutes: int = Field(gt=0)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    indoor_outdoor: str = "unknown"
    suitability_tags: list[str] = Field(default_factory=list)
    data_confidence: float = Field(default=1.0, ge=0, le=1)
    source: str = "mock"


class PlanItem(BaseModel):
    type: ItemType
    name: str
    start_at: datetime
    end_at: datetime
    poi_id: str | None = None
    travel_from_previous_minutes: int = Field(default=0, ge=0)
    distance_from_previous_meters: int = Field(default=0, ge=0)
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    locked: bool = False

    @model_validator(mode="after")
    def validate_time_order(self) -> "PlanItem":
        if self.end_at <= self.start_at:
            raise ValueError("plan item end_at must be later than start_at")
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("plan item datetimes must be timezone-aware")
        return self


class DayPlan(BaseModel):
    date: date
    theme: str
    primary_area: str
    items: list[PlanItem]
    estimated_cost: Decimal = Decimal("0")
    total_travel_minutes: int = 0
    walking_distance_meters: int = 0
    fatigue_score: float = Field(default=0, ge=0, le=1)


class PlanMetrics(BaseModel):
    preference_match: float = Field(ge=0, le=1)
    diversity: float = Field(ge=0, le=1)
    data_confidence: float = Field(ge=0, le=1)
    total_travel_minutes: int = Field(ge=0)
    walking_distance_meters: int = Field(ge=0)
    estimated_cost: Decimal = Field(ge=0)
    fatigue_score: float = Field(ge=0, le=1)


class Violation(BaseModel):
    type: str
    severity: ViolationSeverity
    message: str
    day: date | None = None
    entity_ids: list[str] = Field(default_factory=list)
    repair_hint: str | None = None


class ValidationResult(BaseModel):
    valid: bool
    violations: list[Violation] = Field(default_factory=list)


class PlanCandidate(BaseModel):
    id: str
    style: PlanStyle
    days: list[DayPlan]
    metrics: PlanMetrics
    validation: ValidationResult | None = None
    score: float | None = None
    reason_facts: list[str] = Field(default_factory=list)


class PlanningRequest(BaseModel):
    trip: TripSpec
    max_replan_rounds: Annotated[int, Field(ge=0, le=5)] = 2


class PlanningResponse(BaseModel):
    status: str
    selected_plan: PlanCandidate | None
    candidates: list[PlanCandidate]
    iterations: int
    message: str | None = None
