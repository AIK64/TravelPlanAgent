"""以显式、可追溯 policy 处理 POI 的未知事实。"""

from datetime import date, timedelta

from travel_agent.domain.models import (
    POIResolution,
    PlanningAssumption,
    PlanningPOI,
    TimeWindow,
    TripSpec,
)
from travel_agent.domain.tool_models import POIFacts, UnknownFactPolicy


PlanningPOI.model_rebuild(_types_namespace={"POIFacts": POIFacts})
POIResolution.model_rebuild(_types_namespace={"POIFacts": POIFacts})


DEFAULT_OPENING_WINDOW = TimeWindow(start="10:00", end="16:00")
DEFAULT_DURATION_MINUTES = 90
CONFIDENCE_DEDUCTION_PER_ASSUMPTION = 0.15
MINIMUM_CONFIDENCE = 0.1


class POIDefaultPolicy:
    """将 Provider 未返回的事实显式默认化，或在 strict 模式拒绝使用。"""

    def __init__(self, unknown_fact_policy: UnknownFactPolicy) -> None:
        self.unknown_fact_policy = unknown_fact_policy

    def resolve(self, facts: POIFacts, trip: TripSpec) -> POIResolution:
        """解析给定行程日期范围内的 POI 规划事实。"""
        opening_windows, missing_opening_window = self._resolve_opening_windows(facts, trip)
        missing_fields = [
            *( ["opening_window"] if missing_opening_window else [] ),
            *( ["duration_minutes"] if facts.suggested_duration_minutes is None else [] ),
            *( ["party_cost"] if facts.average_cost_per_person is None else [] ),
        ]
        if self.unknown_fact_policy is UnknownFactPolicy.STRICT and missing_fields:
            return POIResolution(poi=None, missing_fields=missing_fields)

        assumptions = self._build_assumptions(facts, missing_fields)
        duration_minutes = facts.suggested_duration_minutes or DEFAULT_DURATION_MINUTES
        party_cost = (
            facts.average_cost_per_person * trip.travelers
            if facts.average_cost_per_person is not None
            else None
        )
        data_confidence = max(
            MINIMUM_CONFIDENCE,
            facts.data_confidence - CONFIDENCE_DEDUCTION_PER_ASSUMPTION * len(assumptions),
        )
        return POIResolution(
            poi=PlanningPOI(
                facts=facts,
                opening_windows=opening_windows,
                duration_minutes=duration_minutes,
                party_cost=party_cost,
                assumptions=assumptions,
                data_confidence=data_confidence,
            )
        )

    def _resolve_opening_windows(
        self, facts: POIFacts, trip: TripSpec
    ) -> tuple[dict[date, TimeWindow], bool]:
        windows: dict[date, TimeWindow] = {}
        missing_opening_window = False
        trip_date = trip.start_date
        while trip_date <= trip.end_date:
            if facts.today_opening_date == trip_date and facts.today_opening_window is not None:
                window = facts.today_opening_window
            else:
                window = facts.opening_windows_by_weekday.get(trip_date.weekday())
            if window is None:
                missing_opening_window = True
                window = DEFAULT_OPENING_WINDOW
            windows[trip_date] = window
            trip_date += timedelta(days=1)
        return windows, missing_opening_window

    @staticmethod
    def _build_assumptions(
        facts: POIFacts, missing_fields: list[str]
    ) -> list[PlanningAssumption]:
        details = {
            "opening_window": (
                "10:00-16:00",
                "Provider 未提供适用于全部行程日期的营业时间，按默认 policy 补齐。",
            ),
            "duration_minutes": (
                str(DEFAULT_DURATION_MINUTES),
                "Provider 未提供建议游览时长，按默认 policy 估算。",
            ),
            "party_cost": (
                "unknown",
                "Provider 未提供人均费用，费用保持未知而不进行猜测。",
            ),
        }
        return [
            PlanningAssumption(
                field=field,
                value=details[field][0],
                reason=details[field][1],
                created_at=facts.fetched_at,
            )
            for field in missing_fields
        ]
