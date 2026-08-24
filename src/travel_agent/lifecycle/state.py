from __future__ import annotations

from typing import TypedDict


class PlanLifecycleState(TypedDict):
    session_id: str
    lifecycle_thread_id: str
    status: str
    resume_value: dict | None
    action: dict | None
    edit_patch: dict | None
    edit_summary: dict | None
    impact_result: dict | None
    clarification_round: int
    approval_token: str | None
    message: str | None
    transition_count: int
