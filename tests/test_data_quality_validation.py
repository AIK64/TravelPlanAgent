from datetime import timedelta
from decimal import Decimal
import os
from pathlib import Path
import subprocess
import sys

from travel_agent.domain.models import (
    DayPlan,
    ItemType,
    PlanCandidate,
    PlanItem,
    PlanMetrics,
    PlanStyle,
    PlanningAssumption,
    ValidationResult,
    ValidationStatus,
    Violation,
    ViolationSeverity,
)
from travel_agent.domain.tool_models import ValueSource
from travel_agent.planning.validator import validate_candidate


def test_domain_models_resolve_provenance_types_without_defaults_import():
    """防止 domain 模型依赖规划模块的偶然导入顺序才能生成 schema。"""
    source_root = Path(__file__).parents[1] / "src"
    environment = {**os.environ, "PYTHONPATH": str(source_root)}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "from travel_agent.domain.models import PlanCandidate, PlanningAssumption; "
                "PlanCandidate.model_json_schema(); PlanningAssumption.model_json_schema()"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr


def _candidate_with_activity(
    hangzhou_trip,
    estimated_cost: Decimal | None,
    assumptions: list[PlanningAssumption] | None = None,
) -> PlanCandidate:
    """构造不触发其他硬约束的单活动候选计划。"""
    start_at = hangzhou_trip.arrival.at + timedelta(minutes=90)
    item = PlanItem(
        type=ItemType.ACTIVITY,
        name="灵隐寺",
        start_at=start_at,
        end_at=start_at + timedelta(minutes=60),
        estimated_cost=estimated_cost,
    )
    known_cost = estimated_cost if estimated_cost is not None else Decimal("0")
    return PlanCandidate(
        id="quality-check",
        style=PlanStyle.RELAXED,
        days=[
            DayPlan(
                date=hangzhou_trip.start_date,
                theme="人文",
                primary_area="杭州",
                items=[item],
                estimated_cost=known_cost,
                unknown_cost_item_count=int(estimated_cost is None),
            )
        ],
        metrics=PlanMetrics(
            preference_match=1,
            diversity=1,
            data_confidence=1,
            total_travel_minutes=0,
            walking_distance_meters=0,
            estimated_cost=known_cost,
            unknown_cost_item_count=int(estimated_cost is None),
            fatigue_score=0,
        ),
        assumptions=assumptions or [],
    )


def test_warning_only_result_is_valid_with_warnings():
    """防止未知数据被错误路由为不可行计划。"""
    result = ValidationResult.from_violations(
        [
            Violation(
                type="opening_hours_unverified",
                severity=ViolationSeverity.WARNING,
                message="营业时间来自默认假设",
            )
        ]
    )

    assert result.status is ValidationStatus.VALID_WITH_WARNINGS
    assert result.valid is True
    assert result.model_dump()["valid"] is True


def test_error_result_is_invalid():
    """防止真实硬约束冲突被降级为可选择的告警。"""
    result = ValidationResult.from_violations(
        [Violation(type="time_conflict", severity=ViolationSeverity.ERROR, message="冲突")]
    )

    assert result.status is ValidationStatus.INVALID
    assert result.valid is False
    assert result.model_dump()["valid"] is False


def test_unknown_cost_warns_without_claiming_budget_is_feasible(hangzhou_trip):
    """防止未知费用按零元累计后错误宣称预算通过。"""
    result = validate_candidate(
        hangzhou_trip,
        _candidate_with_activity(hangzhou_trip, estimated_cost=None),
        pois=[],
    )

    assert result.status is ValidationStatus.VALID_WITH_WARNINGS
    assert [(item.type, item.severity) for item in result.violations] == [
        ("budget_unverified", ViolationSeverity.WARNING)
    ]


def test_known_cost_over_budget_is_invalid(hangzhou_trip):
    """防止真实已知费用超预算时只产生数据质量告警。"""
    result = validate_candidate(
        hangzhou_trip,
        _candidate_with_activity(hangzhou_trip, estimated_cost=Decimal("1501")),
        pois=[],
    )

    assert result.status is ValidationStatus.INVALID
    assert [(item.type, item.severity) for item in result.violations] == [
        ("budget_exceeded", ViolationSeverity.ERROR)
    ]


def test_default_assumptions_emit_one_warning_per_field(hangzhou_trip):
    """防止每段路线重复告警，或把默认事实当作 Provider 事实。"""
    assumptions = [
        PlanningAssumption(
            field="opening_window",
            value="10:00-16:00",
            reason="默认营业时间",
            source=ValueSource.DEFAULT,
            created_at=hangzhou_trip.arrival.at,
        ),
        PlanningAssumption(
            field="opening_window",
            value="10:00-16:00",
            reason="另一日期的默认营业时间",
            source=ValueSource.DEFAULT,
            created_at=hangzhou_trip.arrival.at,
        ),
        PlanningAssumption(
            field="duration_minutes",
            value="90",
            reason="默认游览时长",
            source=ValueSource.DEFAULT,
            created_at=hangzhou_trip.arrival.at,
        ),
        PlanningAssumption(
            field="walking_distance",
            value="estimated",
            reason="路线距离来自估算",
            source=ValueSource.DEFAULT,
            created_at=hangzhou_trip.arrival.at,
        ),
    ]

    result = validate_candidate(
        hangzhou_trip,
        _candidate_with_activity(
            hangzhou_trip,
            estimated_cost=Decimal("100"),
            assumptions=assumptions,
        ),
        pois=[],
    )

    assert result.status is ValidationStatus.VALID_WITH_WARNINGS
    assert {(item.type, item.severity) for item in result.violations} == {
        ("opening_hours_unverified", ViolationSeverity.WARNING),
        ("duration_unverified", ViolationSeverity.WARNING),
        ("walking_distance_estimated", ViolationSeverity.WARNING),
    }
