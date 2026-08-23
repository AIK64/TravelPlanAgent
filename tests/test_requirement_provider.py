from __future__ import annotations

from datetime import date, datetime
from types import SimpleNamespace

import pytest

from travel_agent.requirements.errors import (
    RequirementErrorCategory,
    RequirementProviderError,
)
from travel_agent.requirements.models import (
    ClarificationModelInput,
    NaturalPlanningRequest,
    RequirementDraft,
    RequirementIssue,
    RequirementIssueCode,
    RequirementPatch,
)
from travel_agent.requirements.providers.mock import MockRequirementModel
from travel_agent.requirements.providers.openai import OpenAIRequirementModel


@pytest.mark.asyncio
async def test_mock_requirement_model_extracts_supported_hangzhou_fixture():
    model = MockRequirementModel()
    request = NaturalPlanningRequest(
        text=(
            "2026年10月2日到10月4日去杭州，3个人，预算1500元，住西湖东侧，"
            "喜欢自然和美食，2日10:30到杭州东站，4日19:00从杭州东站离开，"
            "灵隐寺必须去，不想太累。"
        ),
        reference_date=date(2026, 8, 23),
    )

    output = await model.parse(request)

    assert output.draft.destination == "杭州"
    assert output.draft.start_date == date(2026, 10, 2)
    assert output.draft.end_date == date(2026, 10, 4)
    assert output.draft.travelers == 3
    assert output.draft.arrival is not None
    assert output.draft.arrival.name == "杭州东站"
    assert output.draft.departure is not None
    assert output.draft.departure.name == "杭州东站"
    assert output.draft.accommodation_name == "西湖东侧"
    assert output.draft.must_visit == ["灵隐寺"]


@pytest.mark.asyncio
async def test_mock_requirement_model_does_not_invent_missing_departure():
    output = await MockRequirementModel().parse(
        NaturalPlanningRequest(
            text="2026年10月2日到10月4日去杭州，2日10:30到杭州东站。",
            reference_date=date(2026, 8, 23),
        )
    )

    assert output.draft.arrival is not None
    assert output.draft.departure is None


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class RaisingResponses:
    def __init__(self, error: Exception):
        self.error = error

    async def parse(self, **_kwargs):
        raise self.error


@pytest.mark.asyncio
async def test_openai_requirement_model_uses_responses_pydantic_parse():
    response = SimpleNamespace(
        status="completed",
        output_parsed=RequirementDraft(destination="杭州"),
        output=[],
        usage=SimpleNamespace(input_tokens=42, output_tokens=18),
    )
    responses = FakeResponses(response)
    client = SimpleNamespace(responses=responses)
    model = OpenAIRequirementModel(client=client, model="configured-model")
    request = NaturalPlanningRequest(
        text="去杭州",
        reference_date=date(2026, 8, 23),
    )

    output = await model.parse(request)

    assert output.draft.destination == "杭州"
    assert output.input_tokens == 42
    assert output.output_tokens == 18
    call = responses.calls[0]
    assert call["model"] == "configured-model"
    assert call["text_format"] is RequirementDraft
    assert call["store"] is False
    assert request.text in call["input"][1]["content"]


@pytest.mark.asyncio
async def test_openai_requirement_model_parses_clarification_patch_only():
    response = SimpleNamespace(
        status="completed",
        output_parsed=RequirementPatch(
            departure={
                "name": "杭州东站",
                "at": "2026-10-04T19:00:00+08:00",
            }
        ),
        output=[],
        usage=SimpleNamespace(input_tokens=25, output_tokens=9),
    )
    responses = FakeResponses(response)
    model = OpenAIRequirementModel(
        client=SimpleNamespace(responses=responses),
        model="configured-model",
    )

    output = await model.parse_clarification(_clarification_input())

    assert output.patch.departure is not None
    assert output.patch.departure.name == "杭州东站"
    assert output.patch.departure.at == datetime.fromisoformat(
        "2026-10-04T19:00:00+08:00"
    )
    call = responses.calls[0]
    assert call["text_format"] is RequirementPatch
    assert call["store"] is False
    assert "departure.name" in call["input"][1]["content"]
    assert _clarification_input().answer in call["input"][1]["content"]


@pytest.mark.asyncio
async def test_openai_requirement_model_maps_refusal_without_exposing_text():
    response = SimpleNamespace(
        status="completed",
        output_parsed=None,
        output=[
            SimpleNamespace(
                content=[SimpleNamespace(type="refusal", refusal="sensitive refusal")]
            )
        ],
        usage=None,
    )
    model = OpenAIRequirementModel(
        client=SimpleNamespace(responses=FakeResponses(response)),
        model="configured-model",
    )

    with pytest.raises(RequirementProviderError) as captured:
        await model.parse(
            NaturalPlanningRequest(
                text="test input",
                reference_date=date(2026, 8, 23),
            )
        )

    assert captured.value.category is RequirementErrorCategory.REFUSAL
    assert "sensitive refusal" not in str(captured.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "category", "code", "retryable"),
    [
        (
            SimpleNamespace(status="incomplete", output_parsed=None, output=[]),
            RequirementErrorCategory.INCOMPLETE,
            "incomplete",
            True,
        ),
        (
            SimpleNamespace(status="completed", output_parsed=None, output=[]),
            RequirementErrorCategory.INVALID_RESPONSE,
            "missing_structured_output",
            True,
        ),
        (
            SimpleNamespace(
                status="completed",
                output_parsed={"unexpected": "field"},
                output=[],
            ),
            RequirementErrorCategory.INVALID_RESPONSE,
            "invalid_structured_output",
            True,
        ),
    ],
)
async def test_openai_requirement_model_rejects_non_successful_output_shapes(
    response,
    category,
    code,
    retryable,
):
    model = OpenAIRequirementModel(
        client=SimpleNamespace(responses=FakeResponses(response)),
        model="configured-model",
    )

    with pytest.raises(RequirementProviderError) as captured:
        await model.parse(
            NaturalPlanningRequest(
                text="去杭州",
                reference_date=date(2026, 8, 23),
            )
        )

    assert captured.value.category is category
    assert captured.value.code == code
    assert captured.value.retryable is retryable


@pytest.mark.asyncio
async def test_openai_requirement_model_accepts_mapping_and_sanitizes_usage():
    response = SimpleNamespace(
        status="completed",
        output_parsed={"destination": "杭州"},
        output="not-a-list",
        usage={"input_tokens": 12, "output_tokens": -1},
    )
    model = OpenAIRequirementModel(
        client=SimpleNamespace(responses=FakeResponses(response)),
        model="configured-model",
    )

    output = await model.parse(
        NaturalPlanningRequest(
            text="去杭州",
            reference_date=date(2026, 8, 23),
        )
    )

    assert output.draft.destination == "杭州"
    assert output.input_tokens == 12
    assert output.output_tokens is None


def _sdk_error(name: str, *, status_code=None) -> Exception:
    error_type = type(name, (Exception,), {})
    error = error_type("sensitive provider detail")
    error.status_code = status_code
    return error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "category", "retryable"),
    [
        (_sdk_error("APITimeoutError"), RequirementErrorCategory.TIMEOUT, True),
        (_sdk_error("RateLimitError"), RequirementErrorCategory.RATE_LIMIT, True),
        (
            _sdk_error("AuthenticationError", status_code=401),
            RequirementErrorCategory.AUTHENTICATION,
            False,
        ),
        (
            _sdk_error("PermissionDeniedError", status_code=403),
            RequirementErrorCategory.PERMISSION,
            False,
        ),
        (
            _sdk_error("APIConnectionError"),
            RequirementErrorCategory.CONNECTION,
            True,
        ),
        (
            _sdk_error("InternalServerError", status_code=500),
            RequirementErrorCategory.UPSTREAM_UNAVAILABLE,
            True,
        ),
        (
            _sdk_error("BadRequestError", status_code=400),
            RequirementErrorCategory.INVALID_REQUEST,
            False,
        ),
        (
            _sdk_error("UnknownSDKError"),
            RequirementErrorCategory.UPSTREAM_UNAVAILABLE,
            True,
        ),
    ],
)
async def test_openai_requirement_model_maps_sdk_errors_safely(
    error,
    category,
    retryable,
):
    model = OpenAIRequirementModel(
        client=SimpleNamespace(responses=RaisingResponses(error)),
        model="configured-model",
    )

    with pytest.raises(RequirementProviderError) as captured:
        await model.parse(
            NaturalPlanningRequest(
                text="private requirement",
                reference_date=date(2026, 8, 23),
            )
        )

    assert captured.value.category is category
    assert captured.value.retryable is retryable
    assert "sensitive provider detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_openai_requirement_model_preserves_normalized_provider_error():
    normalized = RequirementProviderError(
        category=RequirementErrorCategory.REFUSAL,
        code="refusal",
        retryable=False,
        safe_message="安全错误",
    )
    model = OpenAIRequirementModel(
        client=SimpleNamespace(responses=RaisingResponses(normalized)),
        model="configured-model",
    )

    with pytest.raises(RequirementProviderError) as captured:
        await model.parse(
            NaturalPlanningRequest(
                text="去杭州",
                reference_date=date(2026, 8, 23),
            )
        )

    assert captured.value is normalized


def _clarification_input() -> ClarificationModelInput:
    return ClarificationModelInput(
        answer="10月4日19:00从杭州东站离开。",
        current_draft=RequirementDraft(
            destination="杭州",
            start_date=date(2026, 10, 2),
            end_date=date(2026, 10, 4),
        ),
        target_fields=["departure.name", "departure.at"],
        issues=[
            RequirementIssue(
                code=RequirementIssueCode.MISSING,
                field="departure.name",
                message="缺少离开地点",
                question="从哪里离开？",
            )
        ],
        reference_date=date(2026, 8, 23),
        timezone="Asia/Shanghai",
    )
