from __future__ import annotations

from typing import NotRequired, TypedDict


class PlanLifecycleState(TypedDict):
    execution: NotRequired[dict | None]
    session_id: str
    lifecycle_thread_id: str
    status: str
    resume_value: dict | None
    action: dict | None
    edit_patch: dict | None
    edit_summary: dict | None
    impact_result: dict | None
    weather_location: dict | None
    weather_snapshot: dict | None
    weather_risks: list[dict] | None
    weather_event: dict | None
    weather_impact: dict | None
    weather_repair_plan: dict | None
    weather_alternatives: list[dict] | None
    weather_decision: str | None
    clarification_round: int
    approval_token: str | None
    message: str | None
    transition_count: int
