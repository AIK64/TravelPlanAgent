from __future__ import annotations

from types import SimpleNamespace
import asyncio

import pytest

from travel_agent.critique.providers.deepseek import DeepSeekCriticModel
from travel_agent.critique.providers.openai import OpenAICriticModel
from travel_agent.critique.providers._compat import map_provider_error, usage_value
from travel_agent.critique.errors import CriticErrorCategory, CriticProviderError
from travel_agent.critique.errors import CriticUnavailableError
from travel_agent.critique.gateway import CriticGateway
from travel_agent.domain.critique_models import (
    CandidateEvidenceDigest,
    DimensionCritique,
    EvidenceItem,
    EvidenceKind,
    SoftCriticRequest,
    SoftCritique,
    SoftDimension,
)
from travel_agent.domain.models import PlanStyle


def _request() -> tuple[SoftCriticRequest, SoftCritique]:
    evidence = EvidenceItem(
        id="ev_1234567890",
        kind=EvidenceKind.CANDIDATE_METRIC,
        candidate_id="candidate-1",
        field="preference_match",
        value=0.8,
        source="normalized",
        confidence=1,
    )
    digest = CandidateEvidenceDigest(
        candidate_id="candidate-1",
        style=PlanStyle.BALANCED,
        evidence=(evidence,),
        input_chars=100,
    )
    critique = SoftCritique(
        candidate_id="candidate-1",
        dimensions=tuple(
            DimensionCritique(
                dimension=dimension,
                score=80,
                summary="有证据的评价",
                evidence_ids=(evidence.id,),
            )
            for dimension in SoftDimension
        ),
        overall_summary="整体合理",
        tradeoff_evidence_ids=(evidence.id,),
    )
    return SoftCriticRequest(digests=(digest,)), critique


@pytest.mark.asyncio
async def test_openai_critic_uses_structured_outputs_without_storage():
    request, critique = _request()
    calls = []

    class Responses:
        async def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                status="completed",
                output=[],
                output_parsed={"critiques": [critique.model_dump(mode="json")]},
                usage=SimpleNamespace(input_tokens=12, output_tokens=34),
            )

    model = OpenAICriticModel(
        client=SimpleNamespace(responses=Responses()),
        model="explicit-openai-model",
    )
    output = await model.critique(request)
    assert output.critiques == (critique,)
    assert output.input_tokens == 12
    assert calls[0]["store"] is False
    assert calls[0]["text_format"].__name__ == "_CritiqueBatch"


@pytest.mark.asyncio
async def test_deepseek_critic_uses_json_output_and_disables_thinking():
    request, critique = _request()
    calls = []

    class Completions:
        async def create(self, **kwargs):
            calls.append(kwargs)
            payload = {"critiques": [critique.model_dump(mode="json")]}
            import json

            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        finish_reason="stop",
                        message=SimpleNamespace(
                            content=json.dumps(payload, ensure_ascii=False),
                            refusal=None,
                        ),
                    )
                ],
                usage=SimpleNamespace(prompt_tokens=20, completion_tokens=30),
            )

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=Completions())
    )
    model = DeepSeekCriticModel(
        client=client,
        model="explicit-deepseek-model",
    )
    output = await model.critique(request)
    assert output.critiques == (critique,)
    assert output.output_tokens == 30
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize(
    ("error", "category", "retryable"),
    [
        (type("APITimeoutError", (Exception,), {})(), CriticErrorCategory.TIMEOUT, True),
        (type("RateLimitError", (Exception,), {"status_code": 429})(), CriticErrorCategory.RATE_LIMIT, True),
        (type("AuthenticationError", (Exception,), {"status_code": 401})(), CriticErrorCategory.AUTHENTICATION, False),
        (type("PermissionError", (Exception,), {"status_code": 403})(), CriticErrorCategory.PERMISSION, False),
        (type("APIConnectionError", (Exception,), {})(), CriticErrorCategory.CONNECTION, True),
        (type("ServerError", (Exception,), {"status_code": 503})(), CriticErrorCategory.UPSTREAM_UNAVAILABLE, True),
        (type("BadRequestError", (Exception,), {"status_code": 400})(), CriticErrorCategory.INVALID_REQUEST, False),
        (RuntimeError(), CriticErrorCategory.UPSTREAM_UNAVAILABLE, True),
    ],
)
def test_openai_compatible_errors_are_safely_classified(error, category, retryable):
    mapped = map_provider_error(error)
    assert mapped.category is category
    assert mapped.retryable is retryable
    assert usage_value({"input_tokens": 3}, "input_tokens") == 3
    assert usage_value(None, "input_tokens") is None


class _StaticCompletions:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error

    async def create(self, **_kwargs):
        if self.error is not None:
            raise self.error
        return self.response


def _deepseek_client(response=None, error=None):
    return SimpleNamespace(
        chat=SimpleNamespace(
            completions=_StaticCompletions(response=response, error=error)
        )
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "category"),
    [
        (SimpleNamespace(choices=[]), CriticErrorCategory.INVALID_SCHEMA),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="length", message=None)]), CriticErrorCategory.INCOMPLETE),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="content_filter", message=None)]), CriticErrorCategory.REFUSAL),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="insufficient_system_resource", message=None)]), CriticErrorCategory.UPSTREAM_UNAVAILABLE),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="tool_calls", message=None)]), CriticErrorCategory.INVALID_SCHEMA),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="", refusal=None))]), CriticErrorCategory.INVALID_JSON),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="not-json", refusal=None))]), CriticErrorCategory.INVALID_SCHEMA),
        (SimpleNamespace(choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(content="{}", refusal="no"))]), CriticErrorCategory.REFUSAL),
    ],
)
async def test_deepseek_critic_rejects_invalid_upstream_responses(response, category):
    request, _ = _request()
    model = DeepSeekCriticModel(client=_deepseek_client(response), model="test")
    with pytest.raises(CriticProviderError) as raised:
        await model.critique(request)
    assert raised.value.category is category


@pytest.mark.asyncio
async def test_openai_critic_rejects_incomplete_refusal_and_invalid_schema():
    request, _ = _request()

    class Responses:
        def __init__(self, response):
            self.response = response

        async def parse(self, **_kwargs):
            return self.response

    responses = [
        (SimpleNamespace(status="incomplete", output=[]), CriticErrorCategory.INCOMPLETE),
        (
            SimpleNamespace(
                status="completed",
                output=[SimpleNamespace(content=[SimpleNamespace(type="refusal")])],
                output_parsed=None,
            ),
            CriticErrorCategory.REFUSAL,
        ),
        (SimpleNamespace(status="completed", output=[], output_parsed=None), CriticErrorCategory.INVALID_SCHEMA),
        (SimpleNamespace(status="completed", output=[], output_parsed={"critiques": "bad"}), CriticErrorCategory.INVALID_SCHEMA),
    ]
    for response, category in responses:
        model = OpenAICriticModel(
            client=SimpleNamespace(responses=Responses(response)), model="test"
        )
        with pytest.raises(CriticProviderError) as raised:
            await model.critique(request)
        assert raised.value.category is category


@pytest.mark.asyncio
async def test_openai_critic_maps_sdk_failure_without_leaking_response():
    request, _ = _request()

    class Responses:
        async def parse(self, **_kwargs):
            raise RuntimeError("sensitive upstream body")

    model = OpenAICriticModel(
        client=SimpleNamespace(responses=Responses()), model="test"
    )
    with pytest.raises(CriticProviderError) as raised:
        await model.critique(request)
    assert raised.value.category is CriticErrorCategory.UPSTREAM_UNAVAILABLE
    assert "sensitive" not in raised.value.safe_message


def test_deepseek_critic_requires_positive_output_budget():
    with pytest.raises(ValueError, match="max_tokens"):
        DeepSeekCriticModel(client=object(), model="test", max_tokens=0)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0, "max_attempts": 1},
        {"timeout_seconds": 1, "max_attempts": 0},
        {
            "timeout_seconds": 1,
            "max_attempts": 1,
            "base_delay_seconds": -1,
        },
    ],
)
def test_critic_gateway_rejects_invalid_transport_budgets(kwargs):
    gateway_model = SimpleNamespace(name="test", model="test", prompt_version="v1")
    with pytest.raises(ValueError):
        CriticGateway(model=gateway_model, **kwargs)


@pytest.mark.asyncio
async def test_critic_gateway_classifies_asyncio_timeout():
    request, _ = _request()

    class HangingModel:
        name = "hanging"
        model = "hanging-v1"
        prompt_version = "v1"

        async def critique(self, _request):
            await asyncio.sleep(1)

    gateway = CriticGateway(
        model=HangingModel(),
        timeout_seconds=0.001,
        max_attempts=1,
    )
    with pytest.raises(CriticUnavailableError) as raised:
        await gateway.critique(
            request, thread_id="timeout-test", grounding_attempt=1
        )
    assert raised.value.category is CriticErrorCategory.TIMEOUT
