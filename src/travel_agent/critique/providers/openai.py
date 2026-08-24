from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict

from travel_agent.critique.errors import CriticErrorCategory, CriticProviderError
from travel_agent.critique.prompts import CRITIC_PROMPT_VERSION, CRITIC_SYSTEM_PROMPT
from travel_agent.critique.providers._compat import map_provider_error, usage_value
from travel_agent.domain.critique_models import (
    SoftCriticProviderOutput,
    SoftCriticRequest,
    SoftCritique,
)


class _CritiqueBatch(BaseModel):
    model_config = ConfigDict(frozen=True)
    critiques: tuple[SoftCritique, ...]


class OpenAICriticModel:
    name = "openai"
    prompt_version = CRITIC_PROMPT_VERSION

    def __init__(self, *, client: Any, model: str) -> None:
        self._client = client
        self.model = model

    async def critique(self, request: SoftCriticRequest) -> SoftCriticProviderOutput:
        try:
            response = await self._client.responses.parse(
                model=self.model,
                input=[
                    {"role": "system", "content": CRITIC_SYSTEM_PROMPT},
                    {"role": "user", "content": _request_json(request)},
                ],
                text_format=_CritiqueBatch,
                store=False,
            )
        except CriticProviderError:
            raise
        except Exception as error:
            raise map_provider_error(error) from None
        if getattr(response, "status", None) == "incomplete":
            raise CriticProviderError(
                CriticErrorCategory.INCOMPLETE,
                "incomplete",
                True,
                "软质量评审未完成结构化输出",
            )
        if _has_refusal(getattr(response, "output", [])):
            raise CriticProviderError(
                CriticErrorCategory.REFUSAL,
                "refusal",
                False,
                "软质量评审拒绝处理输入",
            )
        parsed = getattr(response, "output_parsed", None)
        if parsed is None:
            raise _invalid_schema("missing_structured_output")
        try:
            batch = parsed if isinstance(parsed, _CritiqueBatch) else _CritiqueBatch.model_validate(parsed)
        except Exception:
            raise _invalid_schema("invalid_structured_output") from None
        usage = getattr(response, "usage", None)
        return SoftCriticProviderOutput(
            critiques=batch.critiques,
            input_tokens=usage_value(usage, "input_tokens"),
            output_tokens=usage_value(usage, "output_tokens"),
        )


def _request_json(request: SoftCriticRequest) -> str:
    return json.dumps(request.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))


def _invalid_schema(code: str) -> CriticProviderError:
    return CriticProviderError(
        CriticErrorCategory.INVALID_SCHEMA,
        code,
        True,
        "软质量评审返回了无效结构",
    )


def _has_refusal(output: object) -> bool:
    if not isinstance(output, list):
        return False
    for item in output:
        content = getattr(item, "content", None)
        if isinstance(content, list) and any(
            getattr(part, "type", None) == "refusal"
            or getattr(part, "refusal", None) is not None
            for part in content
        ):
            return True
    return False
