from datetime import date
import asyncio
import json
from types import SimpleNamespace

import pytest

from travel_agent.config import Settings
from travel_agent.domain.lifecycle_models import EditItemContext, EditModelInput
from travel_agent.edits.gateway import EditGateway
from travel_agent.edits.errors import (
    EditErrorCategory,
    EditProviderError,
    EditUnavailableError,
    map_openai_compatible_error,
)
from travel_agent.edits.providers.deepseek import DeepSeekEditModel
from travel_agent.edits.providers.mock import MockEditModel
from travel_agent.edits.providers.openai import OpenAIEditModel


@pytest.mark.asyncio
async def test_mock_edit_gateway_parses_bounded_move_without_facts():
    gateway = EditGateway(
        model=MockEditModel(), timeout_seconds=1, max_attempts=1
    )
    patch, summary = await gateway.parse(
        EditModelInput(
            text="把博物馆挪到第三天",
            trip_start=date(2026, 10, 2),
            trip_end=date(2026, 10, 4),
            items=(
                EditItemContext(
                    item_id="item-1",
                    poi_id="poi-1",
                    name="博物馆",
                    day=date(2026, 10, 2),
                    index=0,
                ),
            ),
        ),
        session_id="edit-provider",
    )

    assert patch.operations[0].kind.value == "move_item"
    assert patch.operations[0].item_id == "item-1"
    assert patch.operations[0].target_date == date(2026, 10, 4)
    assert summary.provider == "mock"


@pytest.mark.parametrize(
    "env,message",
    [
        ({"EDIT_PROVIDER": "deepseek", "EDIT_MODEL": "deepseek-v4"}, "DEEPSEEK_API_KEY"),
        ({"EDIT_PROVIDER": "openai", "EDIT_MODEL": "gpt-test"}, "OPENAI_API_KEY"),
        ({"PLAN_MAX_AFFECTED_DAYS": "0"}, "PLAN_MAX_AFFECTED_DAYS"),
    ],
)
def test_edit_settings_validate_provider_and_budget(env, message):
    with pytest.raises(ValueError, match=message):
        Settings.from_env(env)


def _input() -> EditModelInput:
    return EditModelInput(
        text="删除博物馆",
        trip_start=date(2026, 10, 2),
        trip_end=date(2026, 10, 4),
        items=(
            EditItemContext(
                item_id="item-1",
                poi_id="poi-1",
                name="博物馆",
                day=date(2026, 10, 2),
                index=0,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_openai_edit_uses_structured_output_without_storage():
    calls = []

    class Responses:
        async def parse(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output_parsed={
                    "operations": [{"kind": "remove_item", "item_id": "item-1"}]
                },
                usage=SimpleNamespace(input_tokens=3, output_tokens=4),
            )

    output = await OpenAIEditModel(
        client=SimpleNamespace(responses=Responses()), model="openai-edit"
    ).parse(_input())

    assert output.patch.operations[0].item_id == "item-1"
    assert output.input_tokens == 3
    assert calls[0]["store"] is False


@pytest.mark.asyncio
async def test_openai_edit_rejects_missing_or_invalid_output_and_maps_errors():
    class Responses:
        def __init__(self, output=None, error=None):
            self.output = output
            self.error = error

        async def parse(self, **_kwargs):
            if self.error:
                raise self.error
            return SimpleNamespace(output_parsed=self.output, usage=None)

    for output in (None, {"operations": []}):
        model = OpenAIEditModel(
            client=SimpleNamespace(responses=Responses(output)), model="test"
        )
        with pytest.raises(EditProviderError):
            await model.parse(_input())
    model = OpenAIEditModel(
        client=SimpleNamespace(responses=Responses(error=RuntimeError("secret"))),
        model="test",
    )
    with pytest.raises(EditProviderError) as raised:
        await model.parse(_input())
    assert "secret" not in raised.value.safe_message


@pytest.mark.asyncio
async def test_deepseek_edit_uses_json_output_and_rejects_invalid_content():
    calls = []

    class Completions:
        def __init__(self, content):
            self.content = content

        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=self.content))],
                usage=SimpleNamespace(prompt_tokens=5, completion_tokens=6),
            )

    payload = json.dumps(
        {"operations": [{"kind": "remove_item", "item_id": "item-1"}]}
    )
    model = DeepSeekEditModel(
        client=SimpleNamespace(
            chat=SimpleNamespace(completions=Completions(payload))
        ),
        model="deepseek-edit",
    )
    output = await model.parse(_input())
    assert output.output_tokens == 6
    assert calls[0]["response_format"] == {"type": "json_object"}
    assert calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}

    for content in (None, "not-json"):
        invalid = DeepSeekEditModel(
            client=SimpleNamespace(
                chat=SimpleNamespace(completions=Completions(content))
            ),
            model="test",
        )
        with pytest.raises(EditProviderError):
            await invalid.parse(_input())


@pytest.mark.parametrize(
    "error,category,retryable",
    [
        (type("APITimeoutError", (Exception,), {})(), EditErrorCategory.TIMEOUT, True),
        (type("RateLimitError", (Exception,), {"status_code": 429})(), EditErrorCategory.RATE_LIMIT, True),
        (type("AuthError", (Exception,), {"status_code": 401})(), EditErrorCategory.AUTHENTICATION, False),
        (type("PermissionError", (Exception,), {"status_code": 403})(), EditErrorCategory.PERMISSION, False),
        (type("ConnectionError", (Exception,), {})(), EditErrorCategory.CONNECTION, True),
        (type("ServerError", (Exception,), {"status_code": 503})(), EditErrorCategory.UPSTREAM_UNAVAILABLE, True),
        (RuntimeError(), EditErrorCategory.INVALID_RESPONSE, True),
    ],
)
def test_edit_error_mapping(error, category, retryable):
    mapped = map_openai_compatible_error(error)
    assert mapped.category is category
    assert mapped.retryable is retryable


@pytest.mark.asyncio
async def test_edit_gateway_timeout_and_provider_retry_are_bounded():
    class Hanging:
        name = "hanging"
        model = "hanging"
        prompt_version = "v1"

        async def parse(self, _request):
            await asyncio.sleep(1)

    with pytest.raises(EditUnavailableError) as timeout:
        await EditGateway(
            model=Hanging(), timeout_seconds=0.001, max_attempts=1
        ).parse(_input(), session_id="timeout")
    assert timeout.value.category is EditErrorCategory.TIMEOUT

    class Failing:
        name = "failing"
        model = "failing"
        prompt_version = "v1"

        async def parse(self, _request):
            raise EditProviderError(
                EditErrorCategory.RATE_LIMIT, "rate_limit", True, "retry"
            )

    with pytest.raises(EditUnavailableError) as failed:
        await EditGateway(
            model=Failing(),
            timeout_seconds=1,
            max_attempts=2,
            base_delay_seconds=0,
            max_delay_seconds=0,
        ).parse(_input(), session_id="retry")
    assert failed.value.attempt_count == 2


@pytest.mark.parametrize(
    "kwargs",
    [
        {"timeout_seconds": 0, "max_attempts": 1},
        {"timeout_seconds": 1, "max_attempts": 0},
    ],
)
def test_edit_gateway_rejects_invalid_budgets(kwargs):
    with pytest.raises(ValueError):
        EditGateway(model=MockEditModel(), **kwargs)
