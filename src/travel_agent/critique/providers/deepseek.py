from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from travel_agent.critique.errors import CriticErrorCategory, CriticProviderError
from travel_agent.critique.prompts import (
    DEEPSEEK_CRITIC_PROMPT_VERSION,
    DEEPSEEK_CRITIC_SYSTEM_PROMPT,
)
from travel_agent.critique.providers._compat import map_provider_error, usage_value
from travel_agent.domain.critique_models import SoftCriticProviderOutput, SoftCriticRequest, SoftCritique


class _CritiqueBatch(BaseModel):
    model_config = ConfigDict(frozen=True)
    critiques: tuple[SoftCritique, ...]


class DeepSeekCriticModel:
    name = "deepseek"
    prompt_version = DEEPSEEK_CRITIC_PROMPT_VERSION

    def __init__(self, *, client: Any, model: str, max_tokens: int = 4096) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self._client = client
        self.model = model
        self._max_tokens = max_tokens

    async def critique(self, request: SoftCriticRequest) -> SoftCriticProviderOutput:
        schema = json.dumps(_CritiqueBatch.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
        system = f"{DEEPSEEK_CRITIC_SYSTEM_PROMPT}\n_CritiqueBatch JSON Schema:\n{schema}"
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))},
                ],
                response_format={"type": "json_object"},
                max_tokens=self._max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except CriticProviderError:
            raise
        except Exception as error:
            raise map_provider_error(error) from None
        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise _invalid(CriticErrorCategory.INVALID_SCHEMA, "missing_choice")
        choice = choices[0]
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "length":
            raise _invalid(CriticErrorCategory.INCOMPLETE, "incomplete")
        if finish_reason == "content_filter":
            raise CriticProviderError(CriticErrorCategory.REFUSAL, "content_filter", False, "软质量评审拒绝处理输入")
        if finish_reason == "insufficient_system_resource":
            raise CriticProviderError(
                CriticErrorCategory.UPSTREAM_UNAVAILABLE,
                "insufficient_system_resource",
                True,
                "软质量评审服务资源暂时不足",
            )
        if finish_reason not in {"stop", None}:
            raise _invalid(CriticErrorCategory.INVALID_SCHEMA, "unexpected_finish_reason")
        message = getattr(choice, "message", None)
        refusal = getattr(message, "refusal", None) if message is not None else None
        if isinstance(refusal, str) and refusal.strip():
            raise CriticProviderError(
                CriticErrorCategory.REFUSAL,
                "refusal",
                False,
                "软质量评审拒绝处理输入",
            )
        content = getattr(message, "content", None) if message is not None else None
        if not isinstance(content, str) or not content.strip():
            raise _invalid(CriticErrorCategory.INVALID_JSON, "empty_content")
        try:
            batch = _CritiqueBatch.model_validate_json(content, strict=True)
        except Exception:
            raise _invalid(CriticErrorCategory.INVALID_SCHEMA, "invalid_structured_output") from None
        usage = getattr(response, "usage", None)
        return SoftCriticProviderOutput(
            critiques=batch.critiques,
            input_tokens=usage_value(usage, "prompt_tokens"),
            output_tokens=usage_value(usage, "completion_tokens"),
        )


def _invalid(category: CriticErrorCategory, code: str) -> CriticProviderError:
    return CriticProviderError(category, code, True, "软质量评审返回了无效 JSON 或结构")
