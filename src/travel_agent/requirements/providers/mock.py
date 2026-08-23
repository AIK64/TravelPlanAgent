from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
import re
from zoneinfo import ZoneInfo

from travel_agent.domain.models import MobilityConstraints, Pace
from travel_agent.requirements.models import (
    AnchorDraft,
    ClarificationModelInput,
    NaturalPlanningRequest,
    RequirementDraft,
    RequirementPatch,
    RequirementPatchProviderOutput,
    RequirementProviderOutput,
)
from travel_agent.requirements.prompts import (
    CLARIFICATION_PROMPT_VERSION,
    REQUIREMENT_PROMPT_VERSION,
)


_DATE_RANGE = re.compile(
    r"(?:(?P<sy>\d{4})年)?(?P<sm>\d{1,2})月(?P<sd>\d{1,2})日?\s*"
    r"(?:到|至|-)\s*(?:(?P<ey>\d{4})年)?(?:(?P<em>\d{1,2})月)?"
    r"(?P<ed>\d{1,2})日"
)
_ARRIVAL = re.compile(
    r"(?:(?P<month>\d{1,2})月)?(?P<day>\d{1,2})日\s*"
    r"(?P<hour>\d{1,2})[:：](?P<minute>\d{2})\s*"
    r"(?:到达|抵达|到)(?P<name>[^，,。；;]+)"
)
_DEPARTURE = re.compile(
    r"(?:(?P<month>\d{1,2})月)?(?P<day>\d{1,2})日\s*"
    r"(?P<hour>\d{1,2})[:：](?P<minute>\d{2})\s*从"
    r"(?P<name>[^，,。；;]+?)(?:离开|出发)"
)


class MockRequirementModel:
    """仅用于离线演示和轨迹测试的有限规则 Fixture，不声称通用语义能力。"""

    name = "mock"
    model = "mock-requirement-v1"
    prompt_version = REQUIREMENT_PROMPT_VERSION
    clarification_prompt_version = CLARIFICATION_PROMPT_VERSION

    async def parse(
        self,
        request: NaturalPlanningRequest,
    ) -> RequirementProviderOutput:
        text = request.text
        start_date, end_date = _extract_date_range(text, request.reference_date)
        default_year = start_date.year if start_date else request.reference_date.year
        default_month = start_date.month if start_date else request.reference_date.month
        timezone = ZoneInfo(request.timezone)

        arrival = _extract_anchor(
            _ARRIVAL,
            text,
            default_year=default_year,
            default_month=default_month,
            timezone=timezone,
        )
        departure = _extract_anchor(
            _DEPARTURE,
            text,
            default_year=default_year,
            default_month=default_month,
            timezone=timezone,
        )
        draft = RequirementDraft(
            destination="杭州" if "杭州" in text else None,
            start_date=start_date,
            end_date=end_date,
            travelers=_extract_travelers(text),
            arrival=arrival,
            departure=departure,
            accommodation_name=_extract_single(text, r"住(?:在)?([^，,。；;]+)"),
            total_budget=_extract_budget(text),
            interests=_extract_terms(text, r"喜欢([^，,。；;]+)"),
            avoid=["高强度"] if "不想太累" in text or "避免高强度" in text else [],
            must_visit=_extract_must_visit(text),
            pace=Pace.RELAXED if "不想太累" in text or "轻松" in text else None,
            mobility=(
                MobilityConstraints(needs_frequent_rest=True)
                if "带父母" in text or "老人" in text
                else None
            ),
        )
        return RequirementProviderOutput(draft=draft)

    async def parse_clarification(
        self,
        request: ClarificationModelInput,
    ) -> RequirementPatchProviderOutput:
        reference_date = (
            request.current_draft.start_date
            or request.current_draft.end_date
            or request.reference_date
        )
        output = await MockRequirementModel().parse(
            NaturalPlanningRequest(
                text=request.answer,
                reference_date=reference_date,
                timezone=request.timezone,
            )
        )
        draft = output.draft
        patch = RequirementPatch(
            destination=draft.destination,
            start_date=draft.start_date,
            end_date=draft.end_date,
            travelers=draft.travelers,
            arrival=draft.arrival,
            departure=draft.departure,
            accommodation_name=draft.accommodation_name,
            total_budget=draft.total_budget,
            interests=draft.interests or None,
            avoid=draft.avoid or None,
            must_visit=draft.must_visit or None,
            pace=draft.pace,
            mobility=draft.mobility,
            daily_start=draft.daily_start,
            daily_end=draft.daily_end,
        )
        return RequirementPatchProviderOutput(patch=patch)


def _extract_date_range(text: str, reference_date: date) -> tuple[date | None, date | None]:
    match = _DATE_RANGE.search(text)
    if match is None:
        return None, None
    start_year = int(match.group("sy") or reference_date.year)
    start_month = int(match.group("sm"))
    end_year = int(match.group("ey") or start_year)
    end_month = int(match.group("em") or start_month)
    try:
        return (
            date(start_year, start_month, int(match.group("sd"))),
            date(end_year, end_month, int(match.group("ed"))),
        )
    except ValueError:
        return None, None


def _extract_anchor(
    pattern: re.Pattern[str],
    text: str,
    *,
    default_year: int,
    default_month: int,
    timezone: ZoneInfo,
) -> AnchorDraft | None:
    match = pattern.search(text)
    if match is None:
        return None
    month = int(match.group("month") or default_month)
    try:
        at = datetime(
            default_year,
            month,
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            tzinfo=timezone,
        )
    except ValueError:
        at = None
    return AnchorDraft(name=match.group("name").strip(), at=at)


def _extract_travelers(text: str) -> int | None:
    numeric = re.search(r"(\d{1,2})\s*(?:个人|人|位)", text)
    if numeric:
        return int(numeric.group(1))
    chinese = {"一": 1, "两": 2, "二": 2, "三": 3, "四": 4, "五": 5}
    match = re.search(r"([一两二三四五])\s*(?:个人|人|位)", text)
    return chinese.get(match.group(1)) if match else None


def _extract_budget(text: str) -> Decimal | None:
    match = re.search(r"预算\s*(\d+(?:\.\d+)?)\s*元", text)
    return Decimal(match.group(1)) if match else None


def _extract_single(text: str, pattern: str) -> str | None:
    match = re.search(pattern, text)
    return match.group(1).strip() if match else None


def _extract_terms(text: str, pattern: str) -> list[str]:
    value = _extract_single(text, pattern)
    if not value:
        return []
    return [item.strip() for item in re.split(r"[、和及]", value) if item.strip()]


def _extract_must_visit(text: str) -> list[str]:
    matches = re.findall(
        r"(?:^|[，,。；;])([^，,。；;]+?)(?:必须去|一定要去)",
        text,
    )
    return [item.strip() for item in matches if item.strip()]
