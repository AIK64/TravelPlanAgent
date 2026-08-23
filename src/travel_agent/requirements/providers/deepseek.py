from __future__ import annotations

import json
from typing import Any

from travel_agent.requirements.errors import (
    RequirementErrorCategory,
    RequirementProviderError,
)
from travel_agent.requirements.models import (
    ClarificationModelInput,
    NaturalPlanningRequest,
    RequirementDraft,
    RequirementPatch,
    RequirementPatchProviderOutput,
    RequirementProviderOutput,
)
from travel_agent.requirements.prompts import (
    DEEPSEEK_CLARIFICATION_PROMPT_VERSION,
    DEEPSEEK_CLARIFICATION_SYSTEM_PROMPT,
    DEEPSEEK_REQUIREMENT_PROMPT_VERSION,
    DEEPSEEK_REQUIREMENT_SYSTEM_PROMPT,
)
from travel_agent.requirements.providers._compat import (
    map_openai_compatible_error,
    usage_value,
)


class DeepSeekRequirementModel:
    """通过 DeepSeek Chat Completions JSON Output 抽取 RequirementDraft。"""

    name = "deepseek"
    prompt_version = DEEPSEEK_REQUIREMENT_PROMPT_VERSION
    clarification_prompt_version = DEEPSEEK_CLARIFICATION_PROMPT_VERSION

    def __init__(self, *, client: Any, model: str, max_tokens: int = 4096) -> None:
        if max_tokens < 1:
            raise ValueError("max_tokens must be positive")
        self._client = client
        self.model = model
        self._max_tokens = max_tokens

    async def parse(
        self,
        request: NaturalPlanningRequest,
    ) -> RequirementProviderOutput:
        user_content = (
            f"reference_date={request.reference_date.isoformat()}\n"
            f"timezone={request.timezone}\n"
            f"requirement={request.text}"
        )
        schema = json.dumps(
            RequirementDraft.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_content = (
            f"{DEEPSEEK_REQUIREMENT_SYSTEM_PROMPT}\n"
            f"RequirementDraft JSON Schema:\n{schema}"
        )
        content, usage = await self._request_json(system_content, user_content)
        try:
            draft = RequirementDraft.model_validate_json(content, strict=True)
        except Exception:
            raise _invalid_output(
                "invalid_structured_output",
                "需求解析服务返回了无效结构",
            ) from None

        return RequirementProviderOutput(
            draft=draft,
            input_tokens=usage_value(usage, "prompt_tokens"),
            output_tokens=usage_value(usage, "completion_tokens"),
        )

    async def parse_clarification(
        self,
        request: ClarificationModelInput,
    ) -> RequirementPatchProviderOutput:
        schema = json.dumps(
            RequirementPatch.model_json_schema(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        system_content = (
            f"{DEEPSEEK_CLARIFICATION_SYSTEM_PROMPT}\n"
            f"RequirementPatch JSON Schema:\n{schema}"
        )
        user_content = json.dumps(
            {
                "reference_date": request.reference_date.isoformat(),
                "timezone": request.timezone,
                "target_fields": request.target_fields,
                "issues": [
                    issue.model_dump(mode="json") for issue in request.issues
                ],
                "current_draft": request.current_draft.model_dump(mode="json"),
                "answer": request.answer,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        content, usage = await self._request_json(system_content, user_content)
        try:
            patch = RequirementPatch.model_validate_json(content, strict=True)
        except Exception:
            raise _invalid_output(
                "invalid_structured_output",
                "需求解析服务返回了无效结构",
            ) from None
        return RequirementPatchProviderOutput(
            patch=patch,
            input_tokens=usage_value(usage, "prompt_tokens"),
            output_tokens=usage_value(usage, "completion_tokens"),
        )

    async def _request_json(
        self,
        system_content: str,
        user_content: str,
    ) -> tuple[str, object]:
        try:
            response = await self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": user_content},
                ],
                response_format={"type": "json_object"},
                max_tokens=self._max_tokens,
                extra_body={"thinking": {"type": "disabled"}},
            )
        except RequirementProviderError:
            raise
        except Exception as error:
            raise map_openai_compatible_error(error) from None

        choices = getattr(response, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise _invalid_output("missing_choice", "需求解析服务未返回结果")
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            raise _invalid_output("missing_message", "需求解析服务未返回结果")
        refusal = getattr(message, "refusal", None)
        if isinstance(refusal, str) and refusal.strip():
            raise RequirementProviderError(
                category=RequirementErrorCategory.REFUSAL,
                code="refusal",
                retryable=False,
                safe_message="需求解析服务拒绝处理该输入",
            )
        finish_reason = getattr(choice, "finish_reason", None)
        if finish_reason == "content_filter":
            raise RequirementProviderError(
                category=RequirementErrorCategory.REFUSAL,
                code="content_filter",
                retryable=False,
                safe_message="需求解析服务拒绝处理该输入",
            )
        if finish_reason == "length":
            raise RequirementProviderError(
                category=RequirementErrorCategory.INCOMPLETE,
                code="incomplete",
                retryable=True,
                safe_message="需求解析服务未能完成 JSON 输出",
            )
        if finish_reason == "insufficient_system_resource":
            raise RequirementProviderError(
                category=RequirementErrorCategory.UPSTREAM_UNAVAILABLE,
                code="insufficient_system_resource",
                retryable=True,
                safe_message="需求解析服务资源暂时不足",
            )
        if finish_reason not in {"stop", None}:
            raise _invalid_output(
                "unexpected_finish_reason",
                "需求解析服务未返回最终 JSON",
            )

        content = getattr(message, "content", None)
        if not isinstance(content, str) or not content.strip():
            raise _invalid_output("empty_content", "需求解析服务返回了空内容")
        try:
            json.loads(content)
        except json.JSONDecodeError:
            raise _invalid_output(
                "invalid_json_output",
                "需求解析服务返回了无效 JSON",
            ) from None
        return content, getattr(response, "usage", None)


def _invalid_output(code: str, safe_message: str) -> RequirementProviderError:
    return RequirementProviderError(
        category=RequirementErrorCategory.INVALID_RESPONSE,
        code=code,
        retryable=True,
        safe_message=safe_message,
    )
