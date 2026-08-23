from __future__ import annotations

from datetime import date
import json
from types import SimpleNamespace

import pytest

from travel_agent.requirements.errors import (
    RequirementErrorCategory,
    RequirementProviderError,
)
from travel_agent.requirements.models import NaturalPlanningRequest
from travel_agent.requirements.models import (
    ClarificationModelInput,
    RequirementDraft,
    RequirementIssue,
    RequirementIssueCode,
)
from travel_agent.requirements.providers.deepseek import DeepSeekRequirementModel
from travel_agent.requirements.gateway import RequirementGateway


class FakeCompletions:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _response(
    content: str | None,
    *,
    finish_reason: str = "stop",
    refusal: str | None = None,
):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(prompt_tokens=31, completion_tokens=17),
    )


def _model(completions: FakeCompletions) -> DeepSeekRequirementModel:
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions),
    )
    return DeepSeekRequirementModel(client=client, model="deepseek-v4-flash")


def _request() -> NaturalPlanningRequest:
    return NaturalPlanningRequest(
        text="2026年10月2日到10月4日去杭州，两人。",
        reference_date=date(2026, 8, 23),
    )


@pytest.mark.asyncio
async def test_deepseek_model_uses_json_output_and_validates_draft():
    completions = FakeCompletions(
        response=_response(
            json.dumps(
                {
                    "destination": "杭州",
                    "start_date": "2026-10-02",
                    "end_date": "2026-10-04",
                    "travelers": 2,
                },
                ensure_ascii=False,
            )
        )
    )
    model = _model(completions)

    output = await model.parse(_request())

    assert output.draft.destination == "杭州"
    assert output.draft.start_date == date(2026, 10, 2)
    assert output.input_tokens == 31
    assert output.output_tokens == 17
    call = completions.calls[0]
    assert call["model"] == "deepseek-v4-flash"
    assert call["response_format"] == {"type": "json_object"}
    assert call["max_tokens"] == 4096
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "json" in call["messages"][0]["content"].casefold()
    assert "destination" in call["messages"][0]["content"]
    assert _request().text in call["messages"][1]["content"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "category", "code", "retryable"),
    [
        (
            _response(None),
            RequirementErrorCategory.INVALID_RESPONSE,
            "empty_content",
            True,
        ),
        (
            _response(""),
            RequirementErrorCategory.INVALID_RESPONSE,
            "empty_content",
            True,
        ),
        (
            _response("not-json"),
            RequirementErrorCategory.INVALID_RESPONSE,
            "invalid_json_output",
            True,
        ),
        (
            _response('{"unexpected":"field"}'),
            RequirementErrorCategory.INVALID_RESPONSE,
            "invalid_structured_output",
            True,
        ),
        (
            _response('{"travelers":"2"}'),
            RequirementErrorCategory.INVALID_RESPONSE,
            "invalid_structured_output",
            True,
        ),
        (
            _response("{}", finish_reason="length"),
            RequirementErrorCategory.INCOMPLETE,
            "incomplete",
            True,
        ),
        (
            _response(None, finish_reason="content_filter"),
            RequirementErrorCategory.REFUSAL,
            "content_filter",
            False,
        ),
        (
            _response(None, finish_reason="insufficient_system_resource"),
            RequirementErrorCategory.UPSTREAM_UNAVAILABLE,
            "insufficient_system_resource",
            True,
        ),
        (
            _response(None, finish_reason="tool_calls"),
            RequirementErrorCategory.INVALID_RESPONSE,
            "unexpected_finish_reason",
            True,
        ),
        (
            _response(None, refusal="sensitive refusal"),
            RequirementErrorCategory.REFUSAL,
            "refusal",
            False,
        ),
    ],
)
async def test_deepseek_model_normalizes_invalid_output(
    response,
    category,
    code,
    retryable,
):
    model = _model(FakeCompletions(response=response))

    with pytest.raises(RequirementProviderError) as captured:
        await model.parse(_request())

    assert captured.value.category is category
    assert captured.value.code == code
    assert captured.value.retryable is retryable
    assert "sensitive refusal" not in str(captured.value)


@pytest.mark.asyncio
async def test_deepseek_model_rejects_missing_choices():
    model = _model(
        FakeCompletions(
            response=SimpleNamespace(choices=[], usage=None),
        )
    )

    with pytest.raises(RequirementProviderError) as captured:
        await model.parse(_request())

    assert captured.value.category is RequirementErrorCategory.INVALID_RESPONSE
    assert captured.value.code == "missing_choice"


@pytest.mark.asyncio
async def test_deepseek_model_maps_sdk_error_without_exposing_provider_detail():
    error_type = type("RateLimitError", (Exception,), {})
    error = error_type("sensitive provider detail")
    error.status_code = 429
    model = _model(FakeCompletions(error=error))

    with pytest.raises(RequirementProviderError) as captured:
        await model.parse(_request())

    assert captured.value.category is RequirementErrorCategory.RATE_LIMIT
    assert captured.value.retryable is True
    assert "sensitive provider detail" not in str(captured.value)


@pytest.mark.asyncio
async def test_deepseek_prompt_version_enters_gateway_summary():
    model = _model(
        FakeCompletions(
            response=_response('{"destination":"杭州"}'),
        )
    )
    gateway = RequirementGateway(
        model=model,
        timeout_seconds=1,
        max_attempts=1,
        base_delay_seconds=0,
        max_delay_seconds=0,
    )

    result = await gateway.parse(_request(), thread_id="deepseek-summary")

    assert result.summary.provider == "deepseek"
    assert result.summary.model == "deepseek-v4-flash"
    assert result.summary.prompt_version == "requirement-parser-deepseek-v1"


@pytest.mark.asyncio
async def test_deepseek_model_parses_clarification_patch_with_json_output():
    completions = FakeCompletions(
        response=_response(
            '{"departure":{"name":"杭州东站",'
            '"at":"2026-10-04T19:00:00+08:00"}}'
        )
    )
    model = _model(completions)

    output = await model.parse_clarification(
        ClarificationModelInput(
            answer="10月4日19:00从杭州东站离开。",
            current_draft=RequirementDraft(destination="杭州"),
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
    )

    assert output.patch.departure is not None
    assert output.patch.departure.name == "杭州东站"
    call = completions.calls[0]
    assert call["response_format"] == {"type": "json_object"}
    assert call["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "RequirementPatch" in call["messages"][0]["content"]
    assert "departure.name" in call["messages"][1]["content"]
